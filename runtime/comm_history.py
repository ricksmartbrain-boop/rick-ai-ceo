#!/usr/bin/env python3
"""Unified communication-history layer.

Public API
----------
get_history(recipient, modality_filter=None, days_back=90) → list[Touch]
render_for_prompt(history, max_chars=3000) → str
aggregate_by_recipient(days_back=90) → dict[str, list[Touch]]
get_digest_top5(days_back=7) → list[tuple[str, int]]
format_digest_line(top5, days_back=7) → str
is_suppressed(email, days_back=90) → tuple[bool, str]

Touch schema (TypedDict-compatible plain dict)
----------------------------------------------
{
  "ts":           str,   # ISO-8601 UTC
  "modality":     str,   # "email_out" | "email_in" | "bounce" | "voice" | "drip" | "social" | "sequence"
  "direction":    str,   # "out" | "in"
  "channel":      str,   # "email" | "elevenlabs" | "moltbook" | "reddit" | ...
  "subject":      str,   # subject line or touch label or ""
  "body_excerpt": str,   # ≤200 chars
  "status":       str,   # "sent" | "failed" | "bounced" | "replied" | "enrolled" | ...
  "source_log":   str,   # basename of source file
}

Sources (read-only, never written)
-----------------------------------
  ~/rick-vault/operations/email-sends.jsonl
  ~/rick-vault/operations/email-sequence-send.jsonl
  ~/rick-vault/operations/email-bounces.jsonl
  ~/rick-vault/operations/elevenlabs-calls.jsonl
  ~/rick-vault/operations/reply-router.jsonl
  ~/rick-vault/mailbox/triage/inbound-*.jsonl
  ~/rick-vault/projects/email-course-ai-ceo/drip-state.json
  ~/rick-vault/operations/outbound-dispatcher.jsonl
  (future: gmail-sends.jsonl, newsletter-sends.jsonl)

Cache
-----
~/rick-vault/operations/comm-history-cache.jsonl
Per-recipient TTL: 1 hour. Each line: {email, cached_at, touches: [...]}.
File rewritten atomically on any cache-miss fill.

Performance
-----------
Lazy-iterate JSONL; cap at MAX_LINES_PER_SOURCE (5000) per source file.
In-process dict avoids repeated disk reads within the same Python process.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS = DATA_ROOT / "operations"
TRIAGE_DIR = DATA_ROOT / "mailbox" / "triage"
DRIP_STATE = DATA_ROOT / "projects" / "email-course-ai-ceo" / "drip-state.json"
CACHE_FILE = OPS / "comm-history-cache.jsonl"

MAX_LINES_PER_SOURCE: int = 5_000
CACHE_TTL_SECONDS: int = 3_600  # 1 hour

# Statuses that should suppress future outbound
NEGATIVE_STATUSES: frozenset[str] = frozenset({
    "bounced", "complained", "spam_complaint", "unsubscribe", "unsubscribed",
    "not_interested", "hard_bounce", "suppressed", "bounce",
})

# Internal/system address patterns to exclude from digest
_SYSTEM_RE = re.compile(
    r"(noreply|no-reply|bounce|mailer-daemon|postmaster|donotreply|"
    r"@meetrick\.ai|@linkedin\.com|@producthunt\.com|@stripe\.com|"
    r"notifications@|updates@|digest@|hello@digest|newsletter@|"
    r"crew@morning|messages-noreply|info-link\.stripe|"
    r"test@test\.com|debug@test|@example\.com)",
    re.IGNORECASE,
)

# Minimal valid email shape: local@domain.tld
# Excludes image/asset filenames masquerading as emails (e.g. full-white@3x.png)
_FILE_EXT_RE = re.compile(r"\.(png|jpg|jpeg|gif|svg|webp|ico|bmp|pdf|zip|tar|gz|js|css|json|xml|html|htm)$", re.IGNORECASE)
_VALID_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")

# In-process memory cache: email_norm → (cached_at, touches)
_MEM_CACHE: dict[str, tuple[datetime, list[dict]]] = {}

# Disk cache loaded once per process
_DISK_CACHE: dict[str, tuple[datetime, list[dict]]] | None = None

# Type alias
Touch = dict[str, str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_touch(
    *,
    ts: str,
    modality: str,
    direction: str,
    channel: str,
    subject: str = "",
    body_excerpt: str = "",
    status: str = "",
    source_log: str = "",
) -> Touch:
    return {
        "ts": ts,
        "modality": modality,
        "direction": direction,
        "channel": channel,
        "subject": subject[:120],
        "body_excerpt": body_excerpt[:200],
        "status": status,
        "source_log": source_log,
    }


def _norm_email(e: str) -> str:
    return (e or "").strip().lower()


def _email_matches(candidate: str, target: str) -> bool:
    return _norm_email(candidate) == _norm_email(target)


def _parse_ts(value: str | None) -> datetime | None:
    """Parse ISO-8601 timestamp to UTC-aware datetime. Returns None on failure."""
    if not value:
        return None
    try:
        s = value.strip()
        # Strip trailing Z, then remove tz offset (+HH:MM or -HH:MM)
        if s.endswith("Z"):
            s = s[:-1]
        s = re.sub(r"[+-]\d{2}:\d{2}$", "", s)
        # Remove fractional seconds beyond microseconds
        if "." in s:
            base, frac = s.split(".", 1)
            s = base + "." + frac[:6]
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _cutoff(days_back: int) -> datetime:
    return _now_utc() - timedelta(days=days_back)


def _ts_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sort_key(t: Touch) -> datetime:
    dt = _parse_ts(t.get("ts"))
    return dt if dt else datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# JSONL lazy reader — capped at MAX_LINES_PER_SOURCE
# ---------------------------------------------------------------------------

def _iter_jsonl(path: Path, cap: int = MAX_LINES_PER_SOURCE) -> Iterator[dict]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh):
                if i >= cap:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


# ---------------------------------------------------------------------------
# Source adapters (read-only; each is fully defensive)
# ---------------------------------------------------------------------------

def _from_email_sends(email: str, cutoff_dt: datetime) -> list[Touch]:
    """email-sends.jsonl — Resend outbound {message_id, status, to, ts}."""
    out = []
    for row in _iter_jsonl(OPS / "email-sends.jsonl"):
        if not _email_matches(row.get("to", ""), email):
            continue
        ts_dt = _parse_ts(row.get("ts"))
        if ts_dt and ts_dt < cutoff_dt:
            continue
        out.append(_mk_touch(
            ts=row.get("ts", ""),
            modality="email_out", direction="out", channel="email",
            subject=row.get("subject", ""),
            status=row.get("status", ""),
            source_log="email-sends.jsonl",
        ))
    return out


def _from_email_sequence_send(email: str, cutoff_dt: datetime) -> list[Touch]:
    """email-sequence-send.jsonl {to, subject, status, timestamp, sequence, step}."""
    out = []
    for row in _iter_jsonl(OPS / "email-sequence-send.jsonl"):
        if not _email_matches(row.get("to", ""), email):
            continue
        ts_str = row.get("timestamp") or row.get("ts", "")
        ts_dt = _parse_ts(ts_str)
        if ts_dt and ts_dt < cutoff_dt:
            continue
        subj = row.get("subject", "")
        seq = row.get("sequence", "")
        step = row.get("step", "")
        label = f"[{seq}/step{step}] {subj}" if seq else subj
        out.append(_mk_touch(
            ts=ts_str,
            modality="email_out", direction="out", channel="email",
            subject=label,
            status=row.get("status", ""),
            source_log="email-sequence-send.jsonl",
        ))
    return out


def _from_email_bounces(email: str, cutoff_dt: datetime) -> list[Touch]:
    """email-bounces.jsonl {ts, email_id, event, to, from, subject, sent_at}."""
    out = []
    for row in _iter_jsonl(OPS / "email-bounces.jsonl"):
        if not _email_matches(row.get("to", ""), email):
            continue
        ts_str = row.get("ts", "")
        ts_dt = _parse_ts(ts_str)
        if ts_dt and ts_dt < cutoff_dt:
            continue
        out.append(_mk_touch(
            ts=ts_str,
            modality="bounce", direction="out", channel="email",
            subject=row.get("subject", ""),
            status=row.get("event", "bounced"),
            source_log="email-bounces.jsonl",
        ))
    return out


def _from_elevenlabs_calls(email: str, cutoff_dt: datetime) -> list[Touch]:
    """elevenlabs-calls.jsonl {ts, lead_id, email, phone, status, duration_s, cost_usd, error}."""
    out = []
    for row in _iter_jsonl(OPS / "elevenlabs-calls.jsonl"):
        if not _email_matches(row.get("email", ""), email):
            continue
        ts_str = row.get("ts", "")
        ts_dt = _parse_ts(ts_str)
        if ts_dt and ts_dt < cutoff_dt:
            continue
        dur = row.get("duration_s", 0)
        excerpt = f"Duration: {dur}s"
        if row.get("error"):
            excerpt += f" | {row['error']}"
        out.append(_mk_touch(
            ts=ts_str,
            modality="voice", direction="out", channel="elevenlabs",
            subject=f"Voice call (lead: {row.get('lead_id', '')})",
            body_excerpt=excerpt,
            status=row.get("status", ""),
            source_log="elevenlabs-calls.jsonl",
        ))
    return out


def _from_reply_router(email: str, cutoff_dt: datetime) -> list[Touch]:
    """reply-router.jsonl {ran_at, file, label, action, email}."""
    out = []
    for row in _iter_jsonl(OPS / "reply-router.jsonl"):
        if not _email_matches(row.get("email", ""), email):
            continue
        ts_str = row.get("ran_at", "")
        ts_dt = _parse_ts(ts_str)
        if ts_dt and ts_dt < cutoff_dt:
            continue
        label = row.get("label", "")
        action = row.get("action", "")
        out.append(_mk_touch(
            ts=ts_str,
            modality="email_in", direction="in", channel="email",
            subject=f"[{label}] inbound classified",
            body_excerpt=f"action={action}",
            status=label,
            source_log="reply-router.jsonl",
        ))
    return out


def _from_inbound_triage(email: str, cutoff_dt: datetime) -> list[Touch]:
    """mailbox/triage/inbound-*.jsonl — raw classified inbound messages."""
    out = []
    triage_files = sorted(TRIAGE_DIR.glob("inbound-*.jsonl"))
    cutoff_date = cutoff_dt.date()

    seen_ids: set[str] = set()
    for f in triage_files:
        # Pre-filter by date in filename
        m = re.search(r"inbound-(\d{4}-\d{2}-\d{2})", f.name)
        if m:
            try:
                if datetime.strptime(m.group(1), "%Y-%m-%d").date() < cutoff_date:
                    continue
            except ValueError:
                pass

        for row in _iter_jsonl(f):
            if not _email_matches(row.get("from", ""), email):
                continue
            mid = row.get("message_id", "")
            if mid and mid in seen_ids:
                continue
            if mid:
                seen_ids.add(mid)
            ts_str = row.get("classified_at") or row.get("ingested_at", "")
            ts_dt = _parse_ts(ts_str)
            if ts_dt and ts_dt < cutoff_dt:
                continue
            raw_body = (row.get("body") or "")[:200].replace("\r\n", " ").replace("\n", " ")
            out.append(_mk_touch(
                ts=ts_str,
                modality="email_in", direction="in", channel="email",
                subject=row.get("subject", ""),
                body_excerpt=raw_body,
                status=row.get("classification", "received"),
                source_log=f.name,
            ))
    return out


def _from_drip_state(email: str, cutoff_dt: datetime) -> list[Touch]:
    """drip-state.json — per-email AI CEO course enrollment state."""
    out = []
    if not DRIP_STATE.exists():
        return out
    try:
        data = json.loads(DRIP_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out

    norm = _norm_email(email)
    for key, val in data.items():
        if _norm_email(key) != norm:
            continue
        enrolled_at = val.get("first_enrolled_at", "")
        last_sent_at = val.get("last_sent_at", "")
        last_day = val.get("last_day_sent", 0)

        ts_dt = _parse_ts(enrolled_at)
        if enrolled_at and (not ts_dt or ts_dt >= cutoff_dt):
            out.append(_mk_touch(
                ts=enrolled_at,
                modality="drip", direction="out", channel="email",
                subject="Enrolled in AI CEO email course",
                body_excerpt=f"last_day_sent={last_day}, last_sent_at={last_sent_at}",
                status="enrolled",
                source_log="drip-state.json",
            ))

        if last_sent_at:
            ts2_dt = _parse_ts(last_sent_at)
            if not ts2_dt or ts2_dt >= cutoff_dt:
                out.append(_mk_touch(
                    ts=last_sent_at,
                    modality="drip", direction="out", channel="email",
                    subject=f"AI CEO course Day {last_day} delivered",
                    status="sent",
                    source_log="drip-state.json",
                ))
    return out


def _from_outbound_dispatcher(email: str, cutoff_dt: datetime) -> list[Touch]:
    """outbound-dispatcher.jsonl {channel, job_id, lead_id, ran_at, result?, status}.

    lead_id is matched against email only when it looks like an email address.
    Social touches without a direct email link are skipped at the per-recipient level.
    """
    out = []
    norm = _norm_email(email)
    for row in _iter_jsonl(OPS / "outbound-dispatcher.jsonl"):
        lead_id = row.get("lead_id", "")
        if "@" not in lead_id or _norm_email(lead_id) != norm:
            continue
        ts_str = row.get("ran_at", "")
        ts_dt = _parse_ts(ts_str)
        if ts_dt and ts_dt < cutoff_dt:
            continue
        channel = row.get("channel", "unknown")
        result = row.get("result", "")
        out.append(_mk_touch(
            ts=ts_str,
            modality="social", direction="out", channel=channel,
            subject=f"Outbound: {channel}",
            body_excerpt=str(result)[:200] if result else "",
            status=row.get("status", ""),
            source_log="outbound-dispatcher.jsonl",
        ))
    return out


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def _cache_load() -> dict[str, tuple[datetime, list[Touch]]]:
    """Read JSONL cache file → in-memory dict."""
    cache: dict[str, tuple[datetime, list[Touch]]] = {}
    if not CACHE_FILE.exists():
        return cache
    for row in _iter_jsonl(CACHE_FILE, cap=200_000):
        em = row.get("email", "")
        cached_at = _parse_ts(row.get("cached_at", ""))
        touches = row.get("touches", [])
        if em and cached_at:
            cache[_norm_email(em)] = (cached_at, touches)
    return cache


def _cache_write(cache: dict[str, tuple[datetime, list[Touch]]]) -> None:
    """Atomically rewrite cache file from in-memory dict."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for em, (cached_at, touches) in cache.items():
                fh.write(json.dumps({
                    "email": em,
                    "cached_at": _ts_str(cached_at),
                    "touches": touches,
                }) + "\n")
        tmp.replace(CACHE_FILE)
    except OSError:
        pass


def _get_disk_cache() -> dict[str, tuple[datetime, list[Touch]]]:
    global _DISK_CACHE
    if _DISK_CACHE is None:
        _DISK_CACHE = _cache_load()
    return _DISK_CACHE


# ---------------------------------------------------------------------------
# Core aggregation (per-recipient, all sources)
# ---------------------------------------------------------------------------

_ALL_ADAPTERS = (
    _from_email_sends,
    _from_email_sequence_send,
    _from_email_bounces,
    _from_elevenlabs_calls,
    _from_reply_router,
    _from_inbound_triage,
    _from_drip_state,
    _from_outbound_dispatcher,
)


def _aggregate_for(email: str, days_back: int = 90) -> list[Touch]:
    """Pull and sort all touches for one recipient. Always uses days_back=90 for caching."""
    cutoff_dt = _cutoff(days_back)
    touches: list[Touch] = []
    for fn in _ALL_ADAPTERS:
        try:
            touches.extend(fn(_norm_email(email), cutoff_dt))
        except Exception:
            pass
    touches.sort(key=_sort_key)
    return touches


def _apply_filter(
    touches: list[Touch],
    modality_filter: list[str] | None,
    days_back: int,
) -> list[Touch]:
    cutoff = _cutoff(days_back)
    result = []
    for t in touches:
        if modality_filter and t["modality"] not in modality_filter:
            continue
        ts_dt = _parse_ts(t.get("ts"))
        if ts_dt and ts_dt < cutoff:
            continue
        result.append(t)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_history(
    recipient_id_or_email: str,
    modality_filter: list[str] | None = None,
    days_back: int = 90,
) -> list[Touch]:
    """Return ordered (oldest-first) list of all touches for recipient.

    Results are cached in-process and on disk for CACHE_TTL_SECONDS (1h).
    modality_filter: if set, only return touches of those modalities
      e.g. ["email_out", "email_in", "bounce"]
    """
    email = _norm_email(recipient_id_or_email)
    if not email:
        return []
    now = _now_utc()

    # 1. In-process cache (fastest)
    if email in _MEM_CACHE:
        cached_at, touches = _MEM_CACHE[email]
        if (now - cached_at).total_seconds() < CACHE_TTL_SECONDS:
            return _apply_filter(touches, modality_filter, days_back)

    # 2. Disk cache
    disk = _get_disk_cache()
    if email in disk:
        cached_at, touches = disk[email]
        if (now - cached_at).total_seconds() < CACHE_TTL_SECONDS:
            _MEM_CACHE[email] = (cached_at, touches)
            return _apply_filter(touches, modality_filter, days_back)

    # 3. Cache miss — aggregate from all sources (base window = 90d)
    touches = _aggregate_for(email, days_back=90)

    # Update both caches
    _MEM_CACHE[email] = (now, touches)
    disk[email] = (now, touches)
    _cache_write(disk)

    return _apply_filter(touches, modality_filter, days_back)


def render_for_prompt(history: list[Touch], max_chars: int = 3000) -> str:
    """Produce a compact PRIOR COMMUNICATIONS block for LLM prompt injection.

    Format:
        --- PRIOR COMMUNICATIONS (N touches) ---
        [YYYY-MM-DD HH:MM] OUT email_out/email | "Subject here" | status: sent
        [YYYY-MM-DD HH:MM] IN  email_in/email  | "Re: Subject"  | status: not_interested
        --- END PRIOR COMMS ---

    Returns "" when history is empty (no block injected).
    Truncates gracefully to max_chars keeping the most-recent touches.
    """
    if not history:
        return ""

    def _fmt_line(t: Touch) -> str:
        ts_dt = _parse_ts(t.get("ts"))
        ts_label = ts_dt.strftime("%Y-%m-%d %H:%M") if ts_dt else (t.get("ts", "") or "")[:16]
        direction = "OUT" if t.get("direction") == "out" else "IN "
        subj = f'"{t["subject"]}"' if t.get("subject") else "(no subject)"
        status_part = f"| status: {t['status']}" if t.get("status") else ""
        body_part = f"| excerpt: {t['body_excerpt'][:60]}" if t.get("body_excerpt") else ""
        return f"[{ts_label}] {direction} {t.get('modality','')}/{t.get('channel','')} | {subj} {status_part} {body_part}".rstrip()

    def _build(touches: list[Touch], total: int) -> str:
        header = f"--- PRIOR COMMUNICATIONS ({total} total, showing {len(touches)}) ---" \
            if len(touches) < total else \
            f"--- PRIOR COMMUNICATIONS ({total} touch{'es' if total != 1 else ''}) ---"
        lines = [header] + [_fmt_line(t) for t in touches] + ["--- END PRIOR COMMS ---"]
        return "\n".join(lines)

    block = _build(history, len(history))
    if len(block) <= max_chars:
        return block

    # Trim from the front: keep the most-recent N touches that fit
    for keep in range(len(history) - 1, 0, -1):
        block = _build(history[-keep:], len(history))
        if len(block) <= max_chars:
            return block

    return block[:max_chars]


def aggregate_by_recipient(days_back: int = 90) -> dict[str, list[Touch]]:
    """Full-scan aggregation across all log sources → dict[email, sorted_touches].

    Excludes internal/system addresses.
    WARNING: reads every log file. Use sparingly; prefer get_history() per-recipient.
    """
    cutoff_dt = _cutoff(days_back)
    all_touches: dict[str, list[Touch]] = {}

    def _add(email_raw: str, touch: Touch) -> None:
        k = _norm_email(email_raw)
        if (k
                and _VALID_EMAIL_RE.match(k)
                and not _FILE_EXT_RE.search(k)
                and not _SYSTEM_RE.search(k)):
            all_touches.setdefault(k, []).append(touch)

    # email-sends.jsonl
    for row in _iter_jsonl(OPS / "email-sends.jsonl"):
        em = row.get("to", "")
        if not em:
            continue
        ts_dt = _parse_ts(row.get("ts"))
        if ts_dt and ts_dt < cutoff_dt:
            continue
        _add(em, _mk_touch(ts=row.get("ts", ""), modality="email_out",
                           direction="out", channel="email",
                           subject=row.get("subject", ""),
                           status=row.get("status", ""),
                           source_log="email-sends.jsonl"))

    # email-sequence-send.jsonl
    for row in _iter_jsonl(OPS / "email-sequence-send.jsonl"):
        em = row.get("to", "")
        if not em:
            continue
        ts_str = row.get("timestamp") or row.get("ts", "")
        ts_dt = _parse_ts(ts_str)
        if ts_dt and ts_dt < cutoff_dt:
            continue
        _add(em, _mk_touch(ts=ts_str, modality="email_out",
                           direction="out", channel="email",
                           subject=row.get("subject", ""),
                           status=row.get("status", ""),
                           source_log="email-sequence-send.jsonl"))

    # email-bounces.jsonl
    for row in _iter_jsonl(OPS / "email-bounces.jsonl"):
        em = row.get("to", "")
        if not em:
            continue
        ts_str = row.get("ts", "")
        ts_dt = _parse_ts(ts_str)
        if ts_dt and ts_dt < cutoff_dt:
            continue
        _add(em, _mk_touch(ts=ts_str, modality="bounce",
                           direction="out", channel="email",
                           subject=row.get("subject", ""),
                           status=row.get("event", "bounced"),
                           source_log="email-bounces.jsonl"))

    # elevenlabs-calls.jsonl
    for row in _iter_jsonl(OPS / "elevenlabs-calls.jsonl"):
        em = row.get("email", "")
        if not em:
            continue
        ts_str = row.get("ts", "")
        ts_dt = _parse_ts(ts_str)
        if ts_dt and ts_dt < cutoff_dt:
            continue
        _add(em, _mk_touch(ts=ts_str, modality="voice",
                           direction="out", channel="elevenlabs",
                           subject=f"Voice call (lead: {row.get('lead_id', '')})",
                           status=row.get("status", ""),
                           source_log="elevenlabs-calls.jsonl"))

    # reply-router.jsonl
    for row in _iter_jsonl(OPS / "reply-router.jsonl"):
        em = row.get("email", "")
        if not em:
            continue
        ts_str = row.get("ran_at", "")
        ts_dt = _parse_ts(ts_str)
        if ts_dt and ts_dt < cutoff_dt:
            continue
        _add(em, _mk_touch(ts=ts_str, modality="email_in",
                           direction="in", channel="email",
                           subject=f"[{row.get('label', '')}] inbound",
                           status=row.get("label", ""),
                           source_log="reply-router.jsonl"))

    # inbound triage files
    cutoff_date = cutoff_dt.date()
    for f in sorted(TRIAGE_DIR.glob("inbound-*.jsonl")):
        m_date = re.search(r"inbound-(\d{4}-\d{2}-\d{2})", f.name)
        if m_date:
            try:
                if datetime.strptime(m_date.group(1), "%Y-%m-%d").date() < cutoff_date:
                    continue
            except ValueError:
                pass
        seen: set[str] = set()
        for row in _iter_jsonl(f):
            em = row.get("from", "")
            if not em:
                continue
            mid = row.get("message_id", "")
            if mid and mid in seen:
                continue
            if mid:
                seen.add(mid)
            ts_str = row.get("classified_at") or row.get("ingested_at", "")
            ts_dt = _parse_ts(ts_str)
            if ts_dt and ts_dt < cutoff_dt:
                continue
            _add(em, _mk_touch(ts=ts_str, modality="email_in",
                               direction="in", channel="email",
                               subject=row.get("subject", ""),
                               status=row.get("classification", "received"),
                               source_log=f.name))

    # Sort each recipient's touches by timestamp
    for em in all_touches:
        all_touches[em].sort(key=_sort_key)

    return all_touches


def get_digest_top5(days_back: int = 7) -> list[tuple[str, int]]:
    """Return top 5 recipients by touch count in last N days (non-system addresses only)."""
    agg = aggregate_by_recipient(days_back=days_back)
    counts = [(email, len(touches)) for email, touches in agg.items()]
    return sorted(counts, key=lambda x: -x[1])[:5]


def format_digest_line(top5: list[tuple[str, int]], days_back: int = 7) -> str:
    """Format top-5 digest for inclusion in briefings/heartbeat reports."""
    if not top5:
        return f"Top recipients ({days_back}d): none"
    lines = [f"**Top 5 recipients by touch count (last {days_back}d):**"]
    for i, (email, count) in enumerate(top5, 1):
        lines.append(f"  {i}. {email} — {count} touch{'es' if count != 1 else ''}")
    return "\n".join(lines)


def is_suppressed(email: str, days_back: int = 90) -> tuple[bool, str]:
    """Check for any negative signal in a recipient's history.

    Returns (True, reason) if suppressed, (False, "") if clear.
    Used as a pre-send gate in newsletter-engine-run.py.
    """
    touches = get_history(email, days_back=days_back)
    for t in reversed(touches):  # most recent first
        if t.get("status") in NEGATIVE_STATUSES:
            return True, f"{t.get('modality')} {t.get('status')} on {(t.get('ts') or '')[:10]}"
        if t.get("modality") == "email_in" and t.get("status") in NEGATIVE_STATUSES:
            return True, f"Replied {t.get('status')} on {(t.get('ts') or '')[:10]}"
    return False, ""


def invalidate_cache(email: str) -> None:
    """Force-expire the in-process and disk cache entry for one recipient."""
    norm = _norm_email(email)
    _MEM_CACHE.pop(norm, None)
    disk = _get_disk_cache()
    if norm in disk:
        disk.pop(norm)
        _cache_write(disk)


# ---------------------------------------------------------------------------
# CLI demo / self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "mykhailomaksymiv@gmail.com"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 90

    print(f"\n{'='*60}")
    print(f"COMM HISTORY: {target}  (last {days}d)")
    print("=" * 60)

    hist = get_history(target, days_back=days)
    print(f"Touches found: {len(hist)}\n")
    for t in hist:
        ts = (t.get("ts") or "")[:19]
        direction = "→" if t.get("direction") == "out" else "←"
        mod = t.get("modality", "")
        subj = t.get("subject", "")[:60]
        st = t.get("status", "")
        print(f"  {ts} {direction} [{mod}] {subj!r} | {st}")

    print()
    print("RENDER FOR PROMPT (3000 char cap):")
    print(render_for_prompt(hist, max_chars=3000))

    suppressed, reason = is_suppressed(target, days_back=days)
    print(f"\nSuppressed: {suppressed}" + (f"  ({reason})" if reason else ""))

    print()
    print("TOP 5 DIGEST (7d):")
    top5 = get_digest_top5(days_back=7)
    print(format_digest_line(top5, 7))
    if not top5:
        print("  (no external touches in last 7d)")
