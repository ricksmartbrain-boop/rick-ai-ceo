"""TIER-3.5 #A12 — Telegram /inbox UI helpers.

Phone-friendly review of counter-pitch + sms + follow-up drafts before any
auto-send. Numbered references survive WITHIN A SESSION via per-chat state at
~/rick-vault/runtime/inbox-ui-state.json.

Public API used by runtime.engine.parse_telegram_text:
  cmd_inbox(connection, chat_id) -> str
  cmd_thread(connection, chat_id, n_str) -> str
  cmd_drafts(chat_id) -> str
  cmd_draft(chat_id, n_str) -> str
  cmd_send(chat_id, n_str, tail) -> str
  cmd_skip(chat_id, n_str, tail) -> str
  cmd_inbox_help() -> str

Every command wraps its work in try/except and returns a Telegram-safe
Markdown string ≤4000 chars. Crashes are caught and surfaced to the operator
rather than re-raising into the dispatcher.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DRAFTS_ROOT = DATA_ROOT / "mailbox" / "drafts"
DRAFTS_SENT_ROOT = DATA_ROOT / "mailbox" / "drafts-sent"
DRAFTS_SKIPPED_ROOT = DATA_ROOT / "mailbox" / "drafts-skipped"
TRIAGE_DIR = DATA_ROOT / "mailbox" / "triage"
OUTBOX_DIR = DATA_ROOT / "mailbox" / "outbox"
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"
STATE_FILE = DATA_ROOT / "runtime" / "inbox-ui-state.json"
LOG_FILE = DATA_ROOT / "operations" / "inbox-ui.jsonl"

TG_MAX = 4000  # Telegram message body limit (we leave a small safety margin under 4096)
DRAFT_KINDS = ("counter-pitch", "sms", "follow-up")
KIND_LABEL = {"counter-pitch": "cp", "sms": "sms", "follow-up": "fu"}


# ---------- utilities --------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(payload: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(payload)
        payload["ts"] = _now_iso()
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except OSError:
        pass


def _truncate(text: str, limit: int = TG_MAX) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 24] + "\n\n_…(truncated)_"


def _md_escape(text: str) -> str:
    """Escape characters that break Telegram Markdown (legacy mode)."""
    if not text:
        return ""
    # Telegram legacy Markdown: only `_`, `*`, `` ` ``, `[` are special. Keep light.
    return re.sub(r"([_*`\[])", r"\\\1", text)


def _short(text: str, n: int) -> str:
    text = (text or "").strip().replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    if len(text) <= n:
        return text
    return text[: max(1, n - 1)].rstrip() + "…"


def _safe_chat_id(chat_id: Any) -> str:
    return str(chat_id) if chat_id else "default"


# ---------- per-chat session state ------------------------------------------

def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        _log({"event": "state-save-failed", "error": str(exc)[:200]})


def _set_list(chat_id: Any, key: str, items: list[dict]) -> None:
    state = _load_state()
    cid = _safe_chat_id(chat_id)
    chat_state = state.setdefault(cid, {})
    chat_state[key] = {"updated_at": _now_iso(), "items": items}
    _save_state(state)


def _get_list(chat_id: Any, key: str) -> list[dict]:
    state = _load_state()
    cid = _safe_chat_id(chat_id)
    bucket = state.get(cid, {}).get(key) or {}
    items = bucket.get("items") or []
    return items if isinstance(items, list) else []


# ---------- draft discovery -------------------------------------------------

def _list_drafts() -> list[dict]:
    """Return all pending drafts across counter-pitch + sms + follow-up dirs.

    Sorted by mtime DESC (newest first). Each item has:
        kind, path (str), body, created_at, thread_id, to (when known).
    """
    items: list[dict] = []
    for kind in DRAFT_KINDS:
        kind_dir = DRAFTS_ROOT / kind
        if not kind_dir.is_dir():
            continue
        try:
            paths = [p for p in kind_dir.iterdir() if p.is_file() and p.suffix == ".json"]
        except OSError:
            continue
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            items.append({
                "kind": kind,
                "path": str(path),
                "body": str(payload.get("body") or ""),
                "subject": str(payload.get("subject") or ""),
                "thread_id": str(payload.get("thread_id") or ""),
                "to": str(payload.get("to") or payload.get("from_email") or ""),
                "created_at": str(payload.get("created_at") or ""),
                "label": str(payload.get("label") or ""),
                "objection_class": str(payload.get("objection_class") or ""),
                "draft_id": str(payload.get("draft_id") or path.stem),
                "_payload": payload,
                "_mtime": path.stat().st_mtime if path.exists() else 0.0,
            })
    items.sort(key=lambda it: it.get("_mtime", 0), reverse=True)
    return items


# ---------- thread discovery -------------------------------------------------

def _list_active_threads(connection: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Top N active email_threads sorted by last_inbound_at DESC.

    Tolerates a missing table (returns empty list).
    """
    try:
        rows = connection.execute(
            """
            SELECT thread_id, gmail_thread_id, prospect_id, subject, status,
                   last_inbound_at, last_outbound_at
            FROM email_threads
            WHERE COALESCE(status, 'open') NOT IN ('closed', 'archived')
            ORDER BY COALESCE(last_inbound_at, '') DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict] = []
    for r in rows:
        out.append({
            "thread_id": r["thread_id"] if "thread_id" in r.keys() else "",
            "gmail_thread_id": r["gmail_thread_id"] if "gmail_thread_id" in r.keys() else "",
            "prospect_id": r["prospect_id"] if "prospect_id" in r.keys() else "",
            "subject": r["subject"] if "subject" in r.keys() else "",
            "status": r["status"] if "status" in r.keys() else "",
            "last_inbound_at": r["last_inbound_at"] if "last_inbound_at" in r.keys() else "",
            "last_outbound_at": r["last_outbound_at"] if "last_outbound_at" in r.keys() else "",
        })
    return out


def _scan_triage_for_thread(thread_id: str, *, max_files: int = 14) -> list[dict]:
    """Walk the most recent triage JSONL files and collect rows for a thread."""
    if not TRIAGE_DIR.is_dir() or not thread_id:
        return []
    try:
        files = sorted(
            (p for p in TRIAGE_DIR.iterdir() if p.is_file() and p.name.startswith("inbound-")),
            key=lambda p: p.name,
            reverse=True,
        )[:max_files]
    except OSError:
        return []
    matches: list[dict] = []
    needle = thread_id.strip()
    for path in files:
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or needle not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("thread_id") or "") == needle or str(row.get("message_id") or "") == needle:
                        matches.append(row)
        except OSError:
            continue
    matches.sort(key=lambda r: str(r.get("received_at") or r.get("ingested_at") or ""))
    return matches


def _get_thread_intent(connection: sqlite3.Connection, thread_id: str) -> str:
    """Best-effort intent label from triage classification (most recent row)."""
    rows = _scan_triage_for_thread(thread_id, max_files=4)
    if not rows:
        return "?"
    last = rows[-1]
    return str(last.get("classification") or last.get("intent_class") or "?") or "?"


# ---------- /inbox -----------------------------------------------------------

def cmd_inbox(connection: sqlite3.Connection, chat_id: Any) -> str:
    try:
        threads = _list_active_threads(connection, limit=10)
        # 2026-04-24: surface drafts breakdown by kind so Vlad sees pending
        # workload at a glance without having to /drafts separately.
        drafts = _list_drafts()
        kind_counts: dict[str, int] = {}
        for d in drafts:
            kind_counts[d.get("kind", "unknown")] = kind_counts.get(d.get("kind", "unknown"), 0) + 1

        lines = ["*Inbox*", ""]

        if drafts:
            badges = " · ".join(f"{KIND_LABEL.get(k, k)}={v}" for k, v in sorted(kind_counts.items()))
            lines.append(f"📝 *Drafts pending*: {len(drafts)} ({badges})")
            lines.append("Use `/drafts` to list, `/draft <n>` to view, `/send <n>` to ship.")
            lines.append("")

        if not threads:
            if not drafts:
                return "_No active email threads + no pending drafts._\nTry `/queue` for upcoming auto-sends."
            return _truncate("\n".join(lines))

        # Persist per-chat numbering so /thread <n> resolves consistently.
        _set_list(chat_id, "threads", threads)
        lines.append("*Active threads* (top 10)")
        for idx, t in enumerate(threads, start=1):
            subj = _short(t.get("subject", "(no subject)"), 60)
            last_in = _short(t.get("last_inbound_at", ""), 19)
            intent = _get_thread_intent(connection, t.get("thread_id", ""))
            sender = _short(t.get("prospect_id", "") or "—", 30)
            lines.append(
                f"`[{idx}]` {_md_escape(subj)} · {_md_escape(sender)} · "
                f"{_md_escape(intent)} · {_md_escape(last_in)}"
            )
        lines.append("")
        lines.append("Use `/thread <n>` to drill in. `/q` for upcoming auto-sends.")
        return _truncate("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        _log({"event": "inbox-failed", "error": str(exc)[:200]})
        return f"_/inbox failed: {str(exc)[:200]}_"


def cmd_queue(connection: sqlite3.Connection, chat_id: Any) -> str:
    """Show what's about to ship in the next 30 minutes — outbound_jobs queued
    + email-sequence drips ready to go. One-screen visibility into "what's
    Rick about to do without me looking."
    """
    try:
        from datetime import timedelta as _td
        cutoff = (datetime.now() + _td(minutes=30)).isoformat(timespec="seconds")
        lines = ["*Queue — next 30 min*", ""]

        # 1) outbound_jobs scheduled within the window
        try:
            rows = connection.execute(
                "SELECT id, channel, lead_id, scheduled_at "
                "FROM outbound_jobs "
                "WHERE status='queued' AND scheduled_at <= ? "
                "ORDER BY scheduled_at ASC LIMIT 12",
                (cutoff,),
            ).fetchall()
            if rows:
                lines.append(f"📨 *Outbound jobs* ({len(rows)})")
                for r in rows:
                    lines.append(
                        f"  • {_md_escape(r['channel'] or '?')} → "
                        f"`{_md_escape(_short(r['lead_id'] or '—', 32))}` "
                        f"at `{_md_escape(_short(r['scheduled_at'], 19))}`"
                    )
                lines.append("")
            else:
                lines.append("📨 *Outbound jobs*: 0 queued in next 30min")
                lines.append("")
        except Exception:
            lines.append("📨 *Outbound jobs*: (table unavailable)")
            lines.append("")

        # 2) email drips ready to send (in outbox/)
        try:
            outbox_root = Path.home() / "rick-vault" / "mailbox" / "outbox"
            email_count = 0
            email_samples: list[str] = []
            if outbox_root.exists():
                for seq_dir in outbox_root.iterdir():
                    if not seq_dir.is_dir():
                        continue
                    for md_path in seq_dir.glob("*.md"):
                        email_count += 1
                        if len(email_samples) < 5:
                            email_samples.append(f"{seq_dir.name}/{md_path.name}")
            if email_count > 0:
                lines.append(f"📧 *Email drips* ({email_count} ready)")
                for s in email_samples:
                    lines.append(f"  • `{_md_escape(_short(s, 60))}`")
                if email_count > 5:
                    lines.append(f"  • _…and {email_count - 5} more_")
                lines.append("")
            else:
                lines.append("📧 *Email drips*: 0 ready")
                lines.append("")
        except Exception:
            pass

        # 3) drafts pending review (not auto-shipping but visible)
        drafts = _list_drafts()
        if drafts:
            kind_counts: dict[str, int] = {}
            for d in drafts:
                kind_counts[d.get("kind", "unknown")] = kind_counts.get(d.get("kind", "unknown"), 0) + 1
            badges = " · ".join(f"{KIND_LABEL.get(k, k)}={v}" for k, v in sorted(kind_counts.items()))
            lines.append(f"📝 *Drafts awaiting review*: {len(drafts)} ({badges}) — `/drafts` to act")

        return _truncate("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        _log({"event": "queue-failed", "error": str(exc)[:200]})
        return f"_/queue failed: {str(exc)[:200]}_"


# ---------- /thread ----------------------------------------------------------

def cmd_thread(connection: sqlite3.Connection, chat_id: Any, n_str: str | None) -> str:
    try:
        if not n_str:
            return "Usage: `/thread <n>` — n is the row number from `/inbox`."
        try:
            n = int(n_str)
        except ValueError:
            return "Usage: `/thread <n>` — n must be an integer."
        threads = _get_list(chat_id, "threads")
        if not threads:
            return "_No thread list in session — run `/inbox` first._"
        if n < 1 or n > len(threads):
            return f"_Thread {n} out of range (1..{len(threads)})._"
        t = threads[n - 1]
        subj = t.get("subject", "(no subject)")
        rows = _scan_triage_for_thread(t.get("thread_id", ""))
        # Combine with thread-level outbound timestamp as a soft signal.
        lines = [
            f"*Thread {n}* — {_md_escape(_short(subj, 100))}",
            f"`thread_id`: `{_md_escape(_short(t.get('thread_id', ''), 80))}`",
            f"_status_: {_md_escape(t.get('status', '') or 'open')} · "
            f"_last_in_: {_md_escape(t.get('last_inbound_at', '') or '—')} · "
            f"_last_out_: {_md_escape(t.get('last_outbound_at', '') or '—')}",
            "",
        ]
        if not rows:
            lines.append("_No triage rows found for this thread._")
            return _truncate("\n".join(lines))
        # Show last 3 messages, oldest first within that window.
        recent = rows[-3:]
        for r in recent:
            sender = r.get("from_name") or r.get("from") or "?"
            ts = r.get("received_at") or r.get("ingested_at") or ""
            body = _short(r.get("body", ""), 600)
            lines.append(f"*{_md_escape(_short(sender, 60))}* · _{_md_escape(_short(ts, 31))}_")
            lines.append(_md_escape(body))
            lines.append("")
        return _truncate("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        _log({"event": "thread-failed", "error": str(exc)[:200]})
        return f"_/thread failed: {str(exc)[:200]}_"


# ---------- /drafts ----------------------------------------------------------

def cmd_drafts(chat_id: Any) -> str:
    try:
        items = _list_drafts()
        if not items:
            return "_No pending drafts in counter-pitch / sms / follow-up._"
        _set_list(chat_id, "drafts", items)
        lines = [f"*Pending drafts* ({len(items)})", ""]
        for idx, it in enumerate(items, start=1):
            kind = KIND_LABEL.get(it["kind"], it["kind"])
            thread = _short(it.get("thread_id") or it.get("draft_id") or "—", 32)
            created = _short(it.get("created_at", ""), 19)
            preview = _short(it.get("body", ""), 100)
            lines.append(
                f"`[{idx}]` {kind} · {_md_escape(thread)} · "
                f"{_md_escape(created)} · {_md_escape(preview)}"
            )
        lines.append("")
        lines.append("Use `/draft <n>` to view, `/send <n>` to approve, `/skip <n>` to reject.")
        return _truncate("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        _log({"event": "drafts-failed", "error": str(exc)[:200]})
        return f"_/drafts failed: {str(exc)[:200]}_"


# ---------- /draft -----------------------------------------------------------

def _resolve_draft(chat_id: Any, n_str: str | None) -> tuple[int | None, dict | None, str]:
    """Return (n, draft, error) — error is "" when draft is found."""
    if not n_str:
        return None, None, "Usage: `/draft <n>` — n is the row number from `/drafts`."
    try:
        n = int(n_str)
    except ValueError:
        return None, None, "Usage: `/draft <n>` — n must be an integer."
    items = _get_list(chat_id, "drafts")
    if not items:
        return None, None, "_No draft list in session — run `/drafts` first._"
    if n < 1 or n > len(items):
        return None, None, f"_Draft {n} out of range (1..{len(items)})._"
    return n, items[n - 1], ""


def cmd_draft(chat_id: Any, n_str: str | None) -> str:
    try:
        n, draft, err = _resolve_draft(chat_id, n_str)
        if err:
            return err
        assert draft is not None and n is not None
        kind = draft["kind"]
        lines = [f"*Draft {n}* ({KIND_LABEL.get(kind, kind)})", ""]
        if draft.get("subject"):
            lines.append(f"*Subject*: {_md_escape(_short(draft['subject'], 200))}")
        if draft.get("to"):
            lines.append(f"*To*: {_md_escape(_short(draft['to'], 80))}")
        if draft.get("thread_id"):
            lines.append(f"*Thread*: `{_md_escape(_short(draft['thread_id'], 80))}`")
        if draft.get("objection_class"):
            lines.append(f"*Objection*: {_md_escape(draft['objection_class'])}")
        if draft.get("label"):
            lines.append(f"*Label*: {_md_escape(draft['label'])}")
        if draft.get("created_at"):
            lines.append(f"_created_: {_md_escape(draft['created_at'])}")
        lines.append("")
        body = draft.get("body") or "_(empty)_"
        # Reserve some budget for the trailer.
        body_room = TG_MAX - sum(len(l) + 1 for l in lines) - 200
        body_room = max(400, body_room)
        if len(body) > body_room:
            body = body[: body_room - 16] + "\n\n_…(truncated)_"
        lines.append(_md_escape(body))
        lines.append("")
        lines.append(f"Approve: `/send {n}`  ·  Reject: `/skip {n}`")
        return _truncate("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        _log({"event": "draft-failed", "error": str(exc)[:200]})
        return f"_/draft failed: {str(exc)[:200]}_"


# ---------- /send + /skip helpers -------------------------------------------

def _suppression_set() -> set[str]:
    if not SUPPRESSION_FILE.exists():
        return set()
    try:
        lines = SUPPRESSION_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    out: set[str] = set()
    for line in lines:
        token = line.strip().split()[0] if line.strip() else ""
        if token and not token.startswith("#"):
            out.add(token.lower())
    return out


def _is_suppressed(handle: str) -> bool:
    """Match against the suppression list. Handles email/phone/loose tokens."""
    if not handle:
        return False
    needle = handle.strip().lower()
    if not needle:
        return False
    sup = _suppression_set()
    if needle in sup:
        return True
    # Phones — strip non-digits to compare.
    digits = re.sub(r"[^\d+]", "", needle)
    if digits and any(re.sub(r"[^\d+]", "", s) == digits for s in sup if s):
        return True
    return False


def _slug_for_outbox(handle: str, fallback: str) -> str:
    handle = (handle or fallback or "draft").lower()
    handle = handle.replace("@", "-at-").replace(".", "-")
    handle = re.sub(r"[^a-z0-9._-]", "-", handle)
    return handle[:60] or "draft"


def _move_draft(draft: dict, dest_root: Path) -> Path | None:
    src = Path(draft["path"])
    if not src.exists():
        return None
    kind = draft["kind"]
    dest_dir = dest_root / kind
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        # Avoid overwrite — append timestamp if collision.
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            dest = dest_dir / f"{stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}{suffix}"
        shutil.move(str(src), str(dest))
        return dest
    except OSError as exc:
        _log({"event": "move-failed", "src": str(src), "error": str(exc)[:200]})
        return None


def _refresh_drafts_index(chat_id: Any, removed_path: str) -> None:
    """Drop the moved item from the in-session draft list so subsequent
    references with the same numbering remain stable for the OTHER items."""
    items = _get_list(chat_id, "drafts")
    if not items:
        return
    for it in items:
        if it.get("path") == removed_path:
            it["_consumed"] = True
            break
    state = _load_state()
    cid = _safe_chat_id(chat_id)
    state.setdefault(cid, {}).setdefault("drafts", {})["items"] = items
    _save_state(state)


def _send_sms(draft: dict) -> tuple[bool, str]:
    payload = draft.get("_payload") or {}
    to = (payload.get("to") or draft.get("to") or "").strip()
    body = (payload.get("body") or draft.get("body") or "").strip()
    if not to or not body:
        return False, "missing to/body"
    try:
        from runtime.integrations import twilio_sms as tw
    except Exception as exc:  # noqa: BLE001
        return False, f"twilio module import failed: {str(exc)[:120]}"
    try:
        sid, token, from_num, source = tw.load_creds()
    except Exception as exc:  # noqa: BLE001
        return False, f"creds load failed: {str(exc)[:120]}"
    if source == "none" or not sid or not token or not from_num:
        return False, f"no Twilio credentials (source={source})"
    if _is_suppressed(to):
        return False, f"recipient on suppression list ({to})"
    try:
        result = tw.send_sms(to, body, dry_run=False)
    except Exception as exc:  # noqa: BLE001
        return False, f"send_sms raised: {str(exc)[:120]}"
    status = str(result.get("status") or "")
    if status in ("sent", "queued", "ok", "success"):
        return True, f"twilio status={status}"
    return False, f"twilio status={status} reason={result.get('reason') or result.get('error') or '?'}"


def _send_counter_pitch(draft: dict) -> tuple[bool, str]:
    """Materialize a counter-pitch draft into the email outbox so the existing
    email-sequence-send.py picks it up on the next sender cycle. We do NOT
    bypass RICK_EMAIL_SEND_LIVE — this just queues the file."""
    payload = draft.get("_payload") or {}
    to = (payload.get("to") or payload.get("from_email") or draft.get("to") or "").strip()
    if not to:
        # Counter-pitch drafts often only carry thread_id; we cannot resolve
        # a recipient safely without a registered enrollment.
        return False, "draft has no recipient (to/from_email missing)"
    if _is_suppressed(to):
        return False, f"recipient on suppression list ({to})"
    subject = (payload.get("subject") or draft.get("subject") or "").strip() or "Re: (no subject)"
    body = (payload.get("body") or "").strip()
    if not body:
        return False, "draft body is empty"
    try:
        target_dir = OUTBOX_DIR / "ad-hoc"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = _slug_for_outbox(to, draft.get("draft_id", ""))
        target = target_dir / f"{stamp}-{slug}-step1.md"
        # Frontmatter + body — email-sequence-send parses the frontmatter for to/subject.
        rendered = (
            "---\n"
            f"to: {to}\n"
            f"subject: {subject}\n"
            f"draft_id: {draft.get('draft_id', '')}\n"
            f"thread_id: {draft.get('thread_id', '')}\n"
            "---\n"
            f"{body}\n"
        )
        target.write_text(rendered, encoding="utf-8")
        return True, f"queued to {target.name}"
    except OSError as exc:
        return False, f"outbox write failed: {str(exc)[:120]}"


def cmd_send(chat_id: Any, n_str: str | None, tail: str = "") -> str:
    try:
        # Defense-in-depth chat_id allowlist gate. parse_telegram_text already
        # checks but only when chat_id is truthy — guard the mutator directly
        # so any future caller (or empty/missing chat_id) cannot fire emails.
        from runtime.engine import authorized_telegram_chat
        if not chat_id or not authorized_telegram_chat(chat_id):
            _log({"event": "send-unauthorized", "chat_id": _safe_chat_id(chat_id)})
            return "(unauthorized)"
        n, draft, err = _resolve_draft(chat_id, n_str)
        if err:
            return err
        assert draft is not None and n is not None
        if draft.get("_consumed"):
            return f"_Draft #{n} was already actioned in this session._"
        kind = draft["kind"]
        if kind == "sms":
            ok, info = _send_sms(draft)
        elif kind == "counter-pitch":
            ok, info = _send_counter_pitch(draft)
        elif kind == "follow-up":
            # Same shape as counter-pitch by default — write to outbox/ad-hoc.
            ok, info = _send_counter_pitch(draft)
        else:
            return f"_Unknown draft kind: {kind}_"
        if not ok:
            _log({"event": "send-rejected", "kind": kind, "n": n, "info": info, "tail": tail[:200]})
            return f"_Refusing to send draft #{n} ({kind}): {info}_"
        moved = _move_draft(draft, DRAFTS_SENT_ROOT)
        _refresh_drafts_index(chat_id, draft["path"])
        _log({
            "event": "send-ok", "kind": kind, "n": n, "info": info,
            "src": draft["path"], "dest": str(moved) if moved else "",
            "tail": tail[:200],
        })
        moved_note = f" → `drafts-sent/{kind}/{moved.name}`" if moved else ""
        return _truncate(
            f"Sent draft #{n} ({KIND_LABEL.get(kind, kind)}). {_md_escape(info)}{moved_note}"
        )
    except Exception as exc:  # noqa: BLE001
        _log({"event": "send-failed", "error": str(exc)[:200]})
        return f"_/send failed: {str(exc)[:200]}_"


def cmd_skip(chat_id: Any, n_str: str | None, tail: str = "") -> str:
    try:
        # Defense-in-depth chat_id allowlist gate (matches cmd_send).
        from runtime.engine import authorized_telegram_chat
        if not chat_id or not authorized_telegram_chat(chat_id):
            _log({"event": "skip-unauthorized", "chat_id": _safe_chat_id(chat_id)})
            return "(unauthorized)"
        n, draft, err = _resolve_draft(chat_id, n_str)
        if err:
            return err
        assert draft is not None and n is not None
        if draft.get("_consumed"):
            return f"_Draft #{n} was already actioned in this session._"
        kind = draft["kind"]
        moved = _move_draft(draft, DRAFTS_SKIPPED_ROOT)
        _refresh_drafts_index(chat_id, draft["path"])
        reason = (tail or "").strip()
        _log({
            "event": "skip", "kind": kind, "n": n,
            "src": draft["path"], "dest": str(moved) if moved else "",
            "reason": reason[:300],
        })
        suffix = f" ({_md_escape(_short(reason, 80))})" if reason else ""
        moved_note = f" → `drafts-skipped/{kind}/{moved.name}`" if moved else ""
        return _truncate(
            f"Skipped draft #{n} ({KIND_LABEL.get(kind, kind)}){suffix}.{moved_note}"
        )
    except Exception as exc:  # noqa: BLE001
        _log({"event": "skip-failed", "error": str(exc)[:200]})
        return f"_/skip failed: {str(exc)[:200]}_"


# ---------- /inbox-help -----------------------------------------------------

def cmd_inbox_help() -> str:
    return (
        "*Inbox UI* — phone-friendly draft review (TIER-3.5 #A12)\n"
        "Aliases: `/i` = `/inbox`, `/q` = `/queue`\n"
        "\n"
        "`/inbox` — top 10 active email threads\n"
        "`/thread <n>` — full subject + last 3 messages from triage\n"
        "`/drafts` — pending drafts across cp / sms / follow-up\n"
        "`/draft <n>` — full body of draft #n\n"
        "`/send <n>` — APPROVE: SMS via Twilio, email → outbox/ad-hoc/\n"
        "`/skip <n> [reason]` — REJECT: move draft to drafts-skipped/\n"
        "\n"
        "_Numbers persist within a session via_ "
        "`~/rick-vault/runtime/inbox-ui-state.json`_._\n"
        "_/send checks suppression.txt + Twilio creds before firing._"
    )
