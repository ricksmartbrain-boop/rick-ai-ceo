#!/usr/bin/env python3
"""Rick Guardian — ONE consolidated deterministic watchdog (no LLM calls).

Designed to run every 30 min via launchd; cheap and silent when healthy.

Checks:
  1. SESSION COST   — estimate per-session USD today from OpenClaw session
                      JSONL usage records (~/.openclaw/agents/*/sessions/).
                      OpenClaw records usage.cost.total as $0.00, so we
                      compute est = tokens x config prices. Alerts when a
                      single session exceeds per_session_cap_usd today, or
                      total est spend exceeds RICK_LLM_DAILY_CAP_USD
                      (default 15, env or rick.env).
  2. SENDER WATCHDOG— alert if the email outbox (~/rick-vault/mailbox/outbox
                      *.md durable queue used by email-sequence-send.py) has
                      pending items older than 24h while delivered=0 in 24h.
  3. WEBHOOK WATCH  — read-only GET /v1/webhook_endpoints; alert while the
                      configured endpoint status != enabled (max once/day).
  4. ZOMBIE DETECTOR— config-driven list of jobs whose log is active but
                      whose side-effect artifacts have not been touched.
  5. CHURN GUARD    — customers with status='canceling' lapsing within 7d.

Alerts go to Telegram ops-alerts (same mechanism as
anthropic-billing-watchdog.py), deduped via the notification_dedupe table
(same pattern as runtime/engine.py notify_operator_deduped). If Telegram is
unreachable, alerts append to ~/rick-vault/control/alerts-pending.jsonl.

Idempotent, deduped, loud on its own failures (exit 2 + self-alert).

Log: ~/rick-vault/operations/rick-guardian.jsonl
Config: config/rick-guardian.json (pricing, caps, zombie job list)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "config" / "rick-guardian.json"
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DB_FILE = DATA_ROOT / "runtime" / "rick-runtime.db"
LOG_FILE = DATA_ROOT / "operations" / "rick-guardian.jsonl"
ALERTS_FALLBACK = DATA_ROOT / "control" / "alerts-pending.jsonl"
OUTBOX_DIR = DATA_ROOT / "mailbox" / "outbox"
EMAIL_SEND_LOGS = [
    DATA_ROOT / "operations" / "email-sends.jsonl",
    DATA_ROOT / "operations" / "email-sequence-send.jsonl",
]
OPENCLAW_AGENTS_DIR = Path.home() / ".openclaw" / "agents"

RICK_ENV_FILES = [
    Path.home() / ".openclaw/workspace/config/rick.env",
    Path.home() / "clawd/config/rick.env",
]
TEAM_CHAT_ID_DEFAULT = "-1003781085932"
OPS_ALERTS_THREAD_ID = "34"
TAIL_BYTES = 1024 * 1024  # only read the last 1MB of large send logs

NOW = datetime.now(timezone.utc)
TS = NOW.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(entry: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry.setdefault("ts", TS)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ---------------------------------------------------------------------------
# Env helper (never logs values) — same pattern as anthropic-billing-watchdog
# ---------------------------------------------------------------------------
def _rick_env(name: str) -> str:
    val = os.getenv(name, "")
    if val:
        return val
    for env_file in RICK_ENV_FILES:
        if not env_file.exists():
            continue
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export "):]
                if line.startswith(name + "="):
                    val = line.split("=", 1)[1].strip()
                    # 2026-07-17: drop trailing inline comment ("VALUE  # note")
                    # the way shell sourcing does, so float()/flag checks get
                    # the clean value.
                    if val[:1] in ('"', "'"):
                        closing = val.find(val[0], 1)
                        if closing > 0:
                            val = val[: closing + 1]
                    else:
                        val = re.split(r"\s+#", val, 1)[0]
                    return val.strip().strip('"').strip("'")
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
# Telegram alerting (ops-alerts topic, billing-watchdog convention)
# ---------------------------------------------------------------------------
def send_telegram_alert(text: str) -> tuple[bool, str]:
    token = _rick_env("RICK_TELEGRAM_BOT_TOKEN")
    if not token:
        return False, "RICK_TELEGRAM_BOT_TOKEN not set"
    chat_id = _rick_env("RICK_TEAM_CHAT_ID") or TEAM_CHAT_ID_DEFAULT
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "message_thread_id": OPS_ALERTS_THREAD_ID,
        "text": text,
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            desc = json.loads(exc.read()).get("description", str(exc))
        except Exception:
            desc = str(exc)
        return False, f"HTTP {exc.code}: {desc}"[:200]
    except Exception as exc:
        return False, str(exc)[:200]
    if body.get("ok"):
        return True, str(body.get("result", {}).get("message_id", "?"))
    return False, str(body.get("description", body))[:200]


# ---------------------------------------------------------------------------
# Dedupe via notification_dedupe (pattern from engine.notify_operator_deduped)
# ---------------------------------------------------------------------------
# Volatile fragments must not evade dedup: churn countdowns ("lapses in 1.9
# day(s)" -> 1.8 -> ...) re-fired the same alert every cycle (2026-07-17).
# Guardian kinds are fully parameterized identities (guardian:churn:{cid}),
# so hashing with all numbers stripped is safe.
_DEDUP_NORMALIZE_PATTERNS = [
    re.compile(r"\(suppressed x\d+ in last \d+h\)"),
    re.compile(r"\b[a-z]+_[0-9a-f]{6,}\b"),                      # ULIDs / hex IDs
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?"),  # ISO ts
    re.compile(r"\b\d+(\.\d+)?\b"),                              # bare numbers / countdowns
]


def _dedup_hash(text: str, kind: str) -> str:
    normalized = (text or "").strip()
    for pat in _DEDUP_NORMALIZE_PATTERNS:
        normalized = pat.sub("", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    return hashlib.sha1(f"{kind}|{normalized}".encode("utf-8")).hexdigest()[:16]

def _dedup_allowed(kind: str, text: str, window_hours: int) -> bool:
    """True if this alert should be sent (first-seen or window elapsed)."""
    h = _dedup_hash(text, kind)
    now_iso = datetime.now().isoformat(timespec="seconds")
    con = sqlite3.connect(DB_FILE, timeout=5)
    try:
        row = con.execute(
            "SELECT last_alerted_at, count_since_alert FROM notification_dedupe "
            "WHERE dedup_hash = ?",
            (h,),
        ).fetchone()
        if row is None:
            con.execute(
                "INSERT INTO notification_dedupe (dedup_hash, kind, first_seen_at, "
                "last_alerted_at, count_since_alert, last_text, total_seen) "
                "VALUES (?, ?, ?, ?, 0, ?, 1)",
                (h, kind, now_iso, now_iso, text[:500]),
            )
            con.commit()
            return True
        try:
            last_alerted = datetime.fromisoformat(row[0])
        except (ValueError, TypeError):
            last_alerted = datetime.now() - timedelta(hours=window_hours + 1)
        if datetime.now() - last_alerted >= timedelta(hours=window_hours):
            con.execute(
                "UPDATE notification_dedupe SET last_alerted_at=?, count_since_alert=0, "
                "last_text=?, total_seen=total_seen+1 WHERE dedup_hash=?",
                (now_iso, text[:500], h),
            )
            con.commit()
            return True
        con.execute(
            "UPDATE notification_dedupe SET count_since_alert=count_since_alert+1, "
            "total_seen=total_seen+1, last_text=? WHERE dedup_hash=?",
            (text[:500], h),
        )
        con.commit()
        return False
    finally:
        con.close()


def alert(kind: str, text: str, window_hours: int = 24) -> str:
    """Dedupe -> Telegram -> fallback file. Returns disposition string."""
    try:
        allowed = _dedup_allowed(kind, text, window_hours)
    except Exception as exc:
        # Dedup must never swallow a real alert
        log({"event": "dedup_error", "kind": kind, "error": str(exc)[:200]})
        allowed = True
    if not allowed:
        log({"event": "alert_suppressed", "kind": kind, "text": text[:200]})
        return "suppressed"
    tg_ok, tg_detail = send_telegram_alert(text)
    if tg_ok:
        log({"event": "alert_sent", "kind": kind, "message_id": tg_detail,
             "text": text[:300]})
        return "sent"
    ALERTS_FALLBACK.parent.mkdir(parents=True, exist_ok=True)
    with ALERTS_FALLBACK.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": TS, "kind": kind, "text": text,
                            "telegram_error": tg_detail}) + "\n")
    log({"event": "alert_fallback_file", "kind": kind,
         "telegram_error": tg_detail, "text": text[:300]})
    return "fallback_file"


# ---------------------------------------------------------------------------
# Check 1: session cost (OpenClaw session JSONL -> est USD today)
# ---------------------------------------------------------------------------
def _price(pricing_sorted: list, model: str, tok: list) -> float | None:
    for key, rates in pricing_sorted:
        if key in model:
            return (tok[0] * rates[0] + tok[1] * rates[1]
                    + tok[2] * rates[2] + tok[3] * rates[3]) / 1e6
    return None

def check_session_cost(cfg: dict, alerts: list) -> dict:
    pricing_sorted = sorted(cfg["session_pricing"].items(),
                            key=lambda kv: len(kv[0]), reverse=True)
    per_session_cap = float(cfg.get("per_session_cap_usd", 5.0))
    daily_cap = float(_rick_env("RICK_LLM_DAILY_CAP_USD")
                      or cfg.get("daily_cap_usd_default", 15.0))
    today = TS[:10]
    midnight = datetime(NOW.year, NOW.month, NOW.day,
                        tzinfo=timezone.utc).timestamp()

    session_files: list[str] = []
    for agent_dir in OPENCLAW_AGENTS_DIR.glob("*/sessions"):
        with os.scandir(agent_dir) as it:
            for e in it:
                if not e.name.endswith(".jsonl") or "trajectory" in e.name:
                    continue
                if e.stat().st_mtime >= midnight:
                    session_files.append(e.path)

    per_session: dict[str, float] = {}
    per_session_models: dict[str, dict] = {}
    unpriced_tokens = 0
    for path in session_files:
        est = 0.0
        models: dict[str, float] = {}
        try:
            with open(path, "r", errors="replace") as f:
                for line in f:
                    if '"usage"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("type") != "message":
                        continue
                    if str(d.get("timestamp", ""))[:10] != today:
                        continue
                    m = d.get("message") or {}
                    if m.get("role") != "assistant":
                        continue
                    u = m.get("usage")
                    if not u:
                        continue
                    tok = [u.get("input", 0) or 0, u.get("output", 0) or 0,
                           u.get("cacheRead", 0) or 0, u.get("cacheWrite", 0) or 0]
                    model = m.get("model") or "unknown"
                    cost = _price(pricing_sorted, model, tok)
                    if cost is None:
                        unpriced_tokens += sum(tok)
                        continue
                    est += cost
                    models[model] = models.get(model, 0.0) + cost
        except OSError:
            continue
        if est > 0:
            key = Path(path).name
            per_session[key] = est
            per_session_models[key] = models

    total = sum(per_session.values())
    top = sorted(per_session.items(), key=lambda kv: kv[1], reverse=True)[:3]

    for name, est in per_session.items():
        if est > per_session_cap:
            model_bits = ", ".join(
                f"{k} ${v:.2f}" for k, v in sorted(
                    per_session_models[name].items(),
                    key=lambda kv: kv[1], reverse=True)[:3])
            alerts.append(alert(
                "guardian:session_cost",
                f"🚨 [rick-guardian] Session {name} est ${est:.2f} today "
                f"({today}) — over ${per_session_cap:.2f}/session cap. "
                f"Models: {model_bits}. Runaway-session risk (Jul 10 pattern).",
            ))
    if total > daily_cap:
        alerts.append(alert(
            "guardian:daily_llm_spend",
            f"🚨 [rick-guardian] Total est LLM spend ${total:.2f} today "
            f"({today}) exceeds daily cap ${daily_cap:.2f} "
            f"(RICK_LLM_DAILY_CAP_USD). Top sessions: "
            + ", ".join(f"{n} ${e:.2f}" for n, e in top),
        ))
    return {
        "sessions_with_spend": len(per_session),
        "files_scanned": len(session_files),
        "est_total_usd": round(total, 2),
        "daily_cap_usd": daily_cap,
        "per_session_cap_usd": per_session_cap,
        "top_sessions": [{"file": n, "est_usd": round(e, 2)} for n, e in top],
        "unpriced_tokens": unpriced_tokens,
    }


# ---------------------------------------------------------------------------
# Check 2: sender watchdog (outbox pending >24h while delivered=0)
# ---------------------------------------------------------------------------
def _tail_lines(path: Path) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > TAIL_BYTES:
                f.seek(size - TAIL_BYTES)
                f.readline()  # drop partial line
            return f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []

def check_sender(alerts: list) -> dict:
    pending = list(OUTBOX_DIR.rglob("*.md")) if OUTBOX_DIR.exists() else []
    oldest_age_h = 0.0
    if pending:
        oldest = min(p.stat().st_mtime for p in pending)
        oldest_age_h = (NOW.timestamp() - oldest) / 3600.0

    cutoff = NOW - timedelta(hours=24)
    delivered_24h = 0
    for log_path in EMAIL_SEND_LOGS:
        for line in _tail_lines(log_path):
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("status") != "sent":
                continue
            ts_raw = str(row.get("ts") or row.get("timestamp") or "")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    # Naive stamps are LOCAL (email-sequence-send.jsonl writes
                    # local isoformat; verified 2026-07-14). Treating them as
                    # UTC aged every send by +7h — false "stuck" alerts.
                    ts = ts.astimezone()
            except ValueError:
                continue
            if ts >= cutoff:
                delivered_24h += 1

    stuck = len(pending) > 0 and oldest_age_h >= 24 and delivered_24h == 0
    if stuck:
        alerts.append(alert(
            "guardian:sender_stuck",
            f"🚨 [rick-guardian] Email queue STUCK: {len(pending)} pending "
            f"draft(s) in mailbox/outbox, oldest {oldest_age_h:.0f}h old, "
            f"0 delivered in 24h. Check email-sequence-send / channel pause.",
        ))
    return {
        "pending": len(pending),
        "oldest_pending_hours": round(oldest_age_h, 1),
        "delivered_24h": delivered_24h,
        "stuck": stuck,
    }


# ---------------------------------------------------------------------------
# Check 3: Stripe webhook endpoint status (read-only GET)
# ---------------------------------------------------------------------------
def check_stripe_webhook(cfg: dict, alerts: list) -> dict:
    webhook_id = cfg.get("stripe_webhook_id", "")
    key = _rick_env("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY not available")
    req = urllib.request.Request(
        "https://api.stripe.com/v1/webhook_endpoints?limit=100",
        headers={"Authorization": f"Bearer {key}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read())
    status = None
    for ep in body.get("data", []):
        if ep.get("id") == webhook_id:
            status = ep.get("status")
            break
    if status != "enabled":
        alerts.append(alert(
            "guardian:stripe_webhook",
            f"🚨 [rick-guardian] Stripe webhook {webhook_id} status="
            f"{status or 'NOT FOUND'} (want: enabled). Payment events are "
            f"not reaching Rick until this is re-enabled in Stripe dashboard.",
            window_hours=24,
        ))
    return {"webhook_id": webhook_id, "status": status or "not_found",
            "endpoints_listed": len(body.get("data", []))}


# ---------------------------------------------------------------------------
# Check: Stripe object provenance (creations must leave a journal trace)
# ---------------------------------------------------------------------------
STRIPE_PROVENANCE_WINDOW_H = 24
# The vault grep must never see the guardian's own outputs: the first alert
# would write the id into them and the journal test would go green on the
# next run purely from our own noise (self-poisoning).
STRIPE_PROVENANCE_GREP_EXCLUDES = (
    "rick-guardian.jsonl",   # LOG_FILE
    "alerts-pending.jsonl",  # ALERTS_FALLBACK
    "guardian.log",          # launchd StandardOutPath (ai.rick.guardian)
    "guardian.err.log",      # launchd StandardErrorPath
    "stripe-provenance-state.json",  # our own seen-state
)
STRIPE_PROVENANCE_STATE = DATA_ROOT / "control" / "stripe-provenance-state.json"

def _stripe_get(path: str, key: str) -> dict:
    req = urllib.request.Request(
        f"https://api.stripe.com{path}",
        headers={"Authorization": f"Bearer {key}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())

def _stripe_id_journaled(obj_id: str) -> bool:
    """Journal test: id appears in the events table or anywhere in the vault."""
    con = sqlite3.connect(DB_FILE, timeout=5)
    try:
        if con.execute(
            "SELECT 1 FROM events WHERE payload_json LIKE '%' || ? || '%' "
            "LIMIT 1",
            (obj_id,),
        ).fetchone() is not None:
            return True
    finally:
        con.close()
    cmd = ["grep", "-rIq"]
    cmd += [f"--exclude={name}" for name in STRIPE_PROVENANCE_GREP_EXCLUDES]
    cmd += ["--", obj_id, str(DATA_ROOT)]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise RuntimeError(
        f"vault grep rc={proc.returncode}: "
        f"{proc.stderr.decode(errors='replace')[:150]}")

def _list_payment_links(key: str) -> tuple[list[dict], bool]:
    """All payment links (active + inactive), paginated. -> (links, truncated)"""
    links: list[dict] = []
    cursor = ""
    for _ in range(5):  # 500 links is already absurd; beyond that, alert
        path = "/v1/payment_links?limit=100" + (
            f"&starting_after={cursor}" if cursor else "")
        body = _stripe_get(path, key)
        data = body.get("data", [])
        links.extend(data)
        if not body.get("has_more") or not data:
            return links, False
        cursor = data[-1].get("id", "")
    return links, True

def check_stripe_provenance(cfg: dict, alerts: list) -> dict:
    """Every Stripe object an agent creates must leave a journal trace.

    2026-07-16 04:01:25 PT an autonomous session created a live $39.99
    payment link on the dead legacy LinguaLive Tools product
    (price_1Ttn2f… + plink_1Ttn3D… on prod_TmIz…) with ZERO local trace —
    no events row, no day-note line, no surviving session transcript
    (04:00-04:02 events are heartbeats only; no rollout was active at
    04:01:25). Transcripts are prunable; Stripe's own ledger is not. So
    this check reads Stripe (read-only GETs) and alerts on any new object
    whose id appears neither in the events table nor anywhere in the vault
    (the journal test). Prompts now demand an ops event per creation
    (subagents.py build_task_prompt); this is the hard detection layer.

    Two detection modes, because the API differs per object:
      - products/prices expose `created` -> true 24h creation window.
      - payment_links have NO created field (verified live 2026-07-16), so
        new = never seen before. First run seeds control/
        stripe-provenance-state.json with every existing link id WITHOUT
        alerting (85 pre-guard links; 72 are unjournaled history on the
        shared ~50-business Stripe account — not Rick incidents). After
        seeding, every unseen id gets the journal test; only ACTIVE
        unjournaled links alert (deactivated = defused, the accepted
        remediation in the 2026-07-16 triage); inactive ones are recorded
        in results. Unjournaled ids are NOT added to the seen-state, so
        they re-alert (once per 24h via dedupe) until journaled or
        deactivated.

    stripe_provenance_known_ids in the config never alert: today's
    already-triaged objects (the parked Bundle trio + the 5 legacy links,
    decisions/lingualive-tools-bundle-triage-2026-07-16.md).
    """
    key = _rick_env("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY not available")
    known = set(cfg.get("stripe_provenance_known_ids", []))
    cutoff = int(NOW.timestamp()) - STRIPE_PROVENANCE_WINDOW_H * 3600

    known_skipped: list[str] = []
    journaled: list[str] = []
    unjournaled: list[str] = []
    truncated: list[str] = []

    def vet(kind: str, obj_id: str, extra: str) -> bool:
        """Journal-test one object; alert if untraced. True = journaled."""
        if _stripe_id_journaled(obj_id):
            journaled.append(obj_id)
            return True
        unjournaled.append(obj_id)
        alerts.append(alert(
            f"guardian:stripe_provenance:{obj_id}",
            f"🚨 [rick-guardian] STRIPE PROVENANCE: {kind} {obj_id} "
            f"({extra}) — its id appears NOWHERE in the events table or "
            f"the vault: an unjournaled live Stripe write (2026-07-16 "
            f"04:01 Bundle pattern). Find who created it; journal it or "
            f"deactivate it.",
        ))
        return False

    # --- products & prices: true created-window ---------------------------
    in_window = 0
    for kind, path in (
        ("product", f"/v1/products?limit=100&created[gte]={cutoff}"),
        ("price", f"/v1/prices?limit=100&created[gte]={cutoff}"),
    ):
        body = _stripe_get(path, key)
        for obj in body.get("data", []):
            obj_id = obj.get("id")
            if not obj_id or int(obj.get("created") or 0) < cutoff:
                continue
            in_window += 1
            if obj_id in known:
                known_skipped.append(obj_id)
                continue
            created_iso = datetime.fromtimestamp(
                int(obj["created"]), tz=timezone.utc).isoformat(
                    timespec="seconds")
            vet(kind, obj_id, f"created {created_iso}")
        if body.get("has_more"):
            truncated.append(kind)

    # --- payment links: first-seen state (no created field in the API) ----
    links, links_truncated = _list_payment_links(key)
    if links_truncated:
        truncated.append("payment_link")
    listed_ids = [lk.get("id", "") for lk in links if lk.get("id")]

    seeded = False
    new_inactive: list[str] = []
    if not STRIPE_PROVENANCE_STATE.exists():
        # Seed silently: everything existing predates the guard. Ghosts in
        # this initial set are history, not incidents (known ids cover the
        # 2026-07-16 triage); alerting on 72 dead links is noise, not signal.
        seeded = True
        seen = set(listed_ids)
        state = {"seeded_at": TS, "payment_link_ids": sorted(seen)}
    else:
        state = json.loads(STRIPE_PROVENANCE_STATE.read_text(encoding="utf-8"))
        seen = set(state.get("payment_link_ids", []))
        for lk in links:
            obj_id = lk.get("id")
            if not obj_id or obj_id in seen:
                continue
            if obj_id in known:
                known_skipped.append(obj_id)
                seen.add(obj_id)
                continue
            active = bool(lk.get("active"))
            if not active:
                # Already defused — record, don't alarm, don't re-test.
                new_inactive.append(obj_id)
                seen.add(obj_id)
                continue
            if vet("payment_link", obj_id,
                   f"ACTIVE, url {lk.get('url', '?')}"):
                seen.add(obj_id)
            # unjournaled: stays out of seen -> re-alerts until resolved
        state["payment_link_ids"] = sorted(seen)
        state["updated_at"] = TS
    STRIPE_PROVENANCE_STATE.parent.mkdir(parents=True, exist_ok=True)
    STRIPE_PROVENANCE_STATE.write_text(
        json.dumps(state, indent=1) + "\n", encoding="utf-8")

    if truncated:
        alerts.append(alert(
            "guardian:stripe_provenance_truncated",
            f"🚨 [rick-guardian] STRIPE PROVENANCE: {'/'.join(truncated)} "
            f"listing truncated (>100 in-window creations or >500 links) — "
            f"provenance not fully checked. That volume is itself an "
            f"incident.",
        ))
    return {"window_hours": STRIPE_PROVENANCE_WINDOW_H,
            "products_prices_in_window": in_window,
            "payment_links_listed": len(listed_ids),
            "links_seeded": seeded,
            "new_inactive_links": new_inactive,
            "known_skipped": known_skipped, "journaled": journaled,
            "unjournaled": unjournaled, "truncated": truncated}


# ---------------------------------------------------------------------------
# Check 4: zombie detector (log active, artifacts untouched)
# ---------------------------------------------------------------------------
def check_zombies(cfg: dict, alerts: list) -> dict:
    results = []
    for job in cfg.get("zombie_jobs", []):
        name = job.get("name", "?")
        quiet_h = float(job.get("max_quiet_hours", 48))
        window = NOW.timestamp() - quiet_h * 3600
        log_path = Path(os.path.expanduser(job.get("log", "")))
        if not log_path.exists():
            results.append({"name": name, "state": "log_missing"})
            continue
        if log_path.stat().st_mtime < window:
            results.append({"name": name, "state": "not_running"})
            continue
        artifacts = [Path(os.path.expanduser(a)) for a in job.get("artifacts", [])]
        fresh = [a for a in artifacts
                 if a.exists() and a.stat().st_mtime >= window]
        if fresh:
            results.append({"name": name, "state": "healthy"})
            continue
        results.append({"name": name, "state": "zombie"})
        alerts.append(alert(
            f"guardian:zombie:{name}",
            f"⚠️ [rick-guardian] ZOMBIE job '{name}': log active within "
            f"{quiet_h:.0f}h but no side-effect artifacts touched "
            f"({', '.join(str(a) for a in artifacts)}). It runs but "
            f"produces nothing.",
        ))
    return {"jobs": results,
            "zombies": [r["name"] for r in results if r["state"] == "zombie"]}


# ---------------------------------------------------------------------------
# Check 5: churn guard (customers canceling with lapse <=7d away)
# ---------------------------------------------------------------------------
_DATE_KEYS = ("cancel_at", "cancel_date", "current_period_end", "lapse_at",
              "ends_at", "period_end")

# Attribution-truth v2 (2026-07-13): LinguaLive is Khrystyna's product —
# Rick runs fulfillment ops only. Label its churn as portfolio so the owner
# is never misled that Rick's MRR is churning.
try:
    sys.path.insert(0, str(ROOT))
    from runtime.revenue_signals import PORTFOLIO_PRODUCT_IDS as _PORTFOLIO_IDS
except Exception:
    _PORTFOLIO_IDS = frozenset({"prod_TV7oz6jtR1ejfd"})  # LinguaLive

def _churn_scope(meta: dict) -> str:
    """'portfolio (LinguaLive)' for Khrystyna's customers, 'Rick MRR' else."""
    if meta.get("product_id") in _PORTFOLIO_IDS:
        return "portfolio (LinguaLive)"
    searchable = " ".join(
        str(meta.get(k) or "")
        for k in ("product_id", "product_name", "source_workflow_title", "product_slug")
    ).lower()
    if "lingualive" in searchable:
        return "portfolio (LinguaLive)"
    # Meetrick customers carry product_id/product_name in metadata; the bare
    # LinguaLive backfills only carry amount_usd=7.99.
    if meta.get("product_id") or meta.get("product_name"):
        return "Rick MRR"
    try:
        amount = float(meta.get("amount_usd") or meta.get("first_purchase_amount_usd") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount == 7.99:
        return "portfolio (LinguaLive)"
    return "Rick MRR"

def _parse_lapse(meta: dict):
    for key in _DATE_KEYS:
        raw = meta.get(key)
        if raw in (None, "", 0):
            continue
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None

def check_churn(alerts: list) -> dict:
    con = sqlite3.connect(DB_FILE, timeout=5)
    try:
        rows = con.execute(
            "SELECT id, email, metadata_json FROM customers "
            "WHERE status = 'canceling'"
        ).fetchall()
    finally:
        con.close()
    lapsing = []
    for cid, email, meta_raw in rows:
        try:
            meta = json.loads(meta_raw or "{}")
        except Exception:
            meta = {}
        lapse = _parse_lapse(meta)
        days_left = None
        if lapse is not None:
            days_left = (lapse - NOW).total_seconds() / 86400.0
        scope = _churn_scope(meta)
        entry = {"id": cid, "email": email, "scope": scope,
                 "days_left": round(days_left, 1) if days_left is not None else None}
        if days_left is not None and days_left <= 7:
            lapsing.append(entry)
            alerts.append(alert(
                f"guardian:churn:{cid}",
                f"🚨 [rick-guardian] CHURN [{scope}]: {email} is canceling and lapses "
                f"in {days_left:.1f} day(s). Save-play window is closing."
                + (" (Not Rick's MRR — LinguaLive fulfillment ops only.)"
                   if scope.startswith("portfolio") else ""),
            ))
        elif lapse is None:
            # No lapse date recorded — could already be inside the 7d window.
            # Err loud (once/day) rather than silently missing a save window.
            lapsing.append({**entry, "note": "no lapse date in metadata"})
            alerts.append(alert(
                f"guardian:churn:{cid}",
                f"🚨 [rick-guardian] CHURN [{scope}]: {email} is canceling but has NO "
                f"lapse date in customers.metadata_json — cannot tell how "
                f"close the deadline is. Sync cancel_at/current_period_end "
                f"from Stripe.",
            ))
    return {"canceling": len(rows), "lapsing_7d_or_unknown": lapsing}


def check_approval_integrity(alerts: list) -> dict:
    """Approvals may only be resolved by the owner (or main Rick session).

    On 2026-07-13 an Iris subagent session resolved live approval
    apr_62dafa5c3a3f via direct sqlite UPDATE. Subagent prompts now forbid
    this (subagents.py build_task_prompt), but prompts are soft — this is
    the hard detection layer.
    """
    try:
        subagents_cfg = json.loads(
            (ROOT / "config" / "subagents.json").read_text(encoding="utf-8")
        )
        personas = {k.lower() for k in subagents_cfg.get("subagents", {})}
        personas |= {
            str(v.get("name", "")).lower()
            for v in subagents_cfg.get("subagents", {}).values()
        }
    except Exception:
        personas = {"iris", "remy", "teagan"}
    personas.discard("")
    # approvals.resolved_at is naive LOCAL time (runner.py convention) — the
    # cutoff must be local too, not guardian's UTC NOW, or the string compare
    # silently excludes everything (kill_switches tz-bug pattern).
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    con = sqlite3.connect(DB_FILE, timeout=5)
    try:
        rows = con.execute(
            "SELECT id, resolved_by, resolved_at FROM approvals "
            "WHERE status != 'open' AND resolved_at >= ?",
            (cutoff,),
        ).fetchall()
        # Raw-sqlite bypass check: every legit resolution goes through
        # engine.resolve_approval, which records an 'approval_resolved' event.
        # A direct UPDATE records none — no matter what resolved_by was spoofed
        # to (incl. 'telegram'). That is exactly how the 2026-07-13 iris
        # incident wrote, so this catches the same move with a faked actor.
        no_event = {
            aid
            for aid, _by, _at in rows
            if con.execute(
                "SELECT 1 FROM events WHERE event_type = 'approval_resolved' "
                "AND payload_json LIKE '%' || ? || '%' LIMIT 1",
                (aid,),
            ).fetchone() is None
        }
    finally:
        con.close()
    violations = []
    for aid, resolved_by, resolved_at in rows:
        actor = (resolved_by or "").lower()
        if actor in personas or actor.startswith("sa_"):
            violations.append({"id": aid, "resolved_by": resolved_by,
                               "resolved_at": resolved_at})
            alerts.append(alert(
                f"guardian:approval_integrity:{aid}",
                f"🚨 [rick-guardian] APPROVAL INTEGRITY: {aid} was resolved by "
                f"subagent '{resolved_by}' at {resolved_at} — approvals are "
                f"owner-only. Review whether the underlying send/decision "
                f"actually had owner consent.",
            ))
        elif aid in no_event:
            violations.append({"id": aid, "resolved_by": resolved_by,
                               "resolved_at": resolved_at, "raw_sqlite": True})
            alerts.append(alert(
                f"guardian:approval_integrity:{aid}",
                f"🚨 [rick-guardian] APPROVAL INTEGRITY: {aid} shows resolved_by "
                f"'{resolved_by}' at {resolved_at} but has NO approval_resolved "
                f"event — every legit path (engine.resolve_approval) records "
                f"one, so this looks like a raw sqlite UPDATE with a spoofed "
                f"actor. Verify owner consent.",
            ))
    return {"resolved_last_24h": len(rows), "subagent_violations": violations}


# ---------------------------------------------------------------------------
# Check: sequence integrity (enrollment cron_ids vs OpenClaw cron store)
# ---------------------------------------------------------------------------
# OpenClaw's own live store — open strictly read-only (mode=ro): the gateway
# owns this WAL database and the guardian must never take a write lock on it.
# RICK_OPENCLAW_STATE_DB override exists for tests against a copy (same
# override pattern as RICK_DATA_ROOT above).
OPENCLAW_STATE_DB = Path(os.getenv(
    "RICK_OPENCLAW_STATE_DB",
    str(Path.home() / ".openclaw" / "state" / "openclaw.sqlite")))
# Alert only while the sequence could still owe sends (Day-30 + slack);
# older broken enrollments are history — logged in results, not re-alerted.
SEQ_ENROLL_MAX_AGE_DAYS = 45

def check_sequence_integrity(alerts: list) -> dict:
    """sequence_enrolled events must stay backed by the OpenClaw cron store.

    2026-06-29 an Iris subagent enrolled diane@factotem.com in
    lingualive_retention_v1 via 4 raw-curl OpenClaw crons. Two fired and were
    consumed (deleteAfterRun), two were disabled by the 2026-07-13 harm-stop
    (customer had canceled) — but the enrollment event and markdown still said
    active/scheduled, and the raw-curl sends bypassed email-sends.jsonl.
    Nothing reconciled the claim against the store, so the sequence looked
    alive for two weeks after it silently died. This is the hard detection
    layer for that gap; it also catches fabricated/never-created cron ids.

    Per recorded cron_id (first match wins):
      ran        — cron_run_logs has a status='ok' run           -> ok
      scheduled  — job exists and enabled=1                      -> ok
      orphaned   — job exists but enabled=0                      -> alert
      failed     — job gone, ran, but never status='ok'          -> alert
      phantom    — job gone and never ran (was it ever created?) -> alert

    Explicit closure (2026-07-16): a 'sequence_closed' customer_event whose
    payload names the enrollment event id ("enrollment_event") marks THAT
    enrollment reconciled (canceled/superseded — no sends owed), so its
    broken crons stop alerting. Per-enrollment only: new enrollments have no
    closure row and still alert.
    """
    con = sqlite3.connect(DB_FILE, timeout=5)
    try:
        rows = con.execute(
            "SELECT ce.id, ce.created_at, ce.payload_json, c.email "
            "FROM customer_events ce "
            "LEFT JOIN customers c ON c.id = ce.customer_id "
            "WHERE ce.event_type = 'sequence_enrolled'"
        ).fetchall()
        closed_markers: dict = {}
        for (closure_raw,) in con.execute(
            "SELECT payload_json FROM customer_events "
            "WHERE event_type = 'sequence_closed'"
        ).fetchall():
            try:
                closure = json.loads(closure_raw or "{}")
            except Exception:
                continue
            ref = closure.get("enrollment_event")
            if ref:
                closed_markers[str(ref)] = str(closure.get("reason") or "")[:160]
    finally:
        con.close()

    if not OPENCLAW_STATE_DB.exists():
        raise RuntimeError(f"OpenClaw cron store missing: {OPENCLAW_STATE_DB}")
    ccon = sqlite3.connect(f"file:{OPENCLAW_STATE_DB}?mode=ro", uri=True,
                           timeout=5)
    enrollments = []
    broken_now: list[str] = []
    broken_stale: list[str] = []
    closed: list[dict] = []
    non_cron = 0
    try:
        for event_id, created_at, payload_raw, email in rows:
            if event_id in closed_markers:
                closed.append({"event": event_id,
                               "reason": closed_markers[event_id]})
                continue
            try:
                payload = json.loads(payload_raw or "{}")
            except Exception:
                payload = {}
            cron_ids = payload.get("cron_ids") or []
            if not cron_ids:
                non_cron += 1  # engine JSON-sequence path; no crons to verify
                continue
            try:
                created = datetime.fromisoformat(
                    str(created_at).replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except ValueError:
                created = NOW
            age_days = (NOW - created).total_seconds() / 86400.0

            states: dict = {}
            for raw_id in cron_ids:
                cron_id = str(raw_id)
                ran_ok = ccon.execute(
                    "SELECT 1 FROM cron_run_logs "
                    "WHERE job_id = ? AND status = 'ok' LIMIT 1",
                    (cron_id,),
                ).fetchone() is not None
                job = ccon.execute(
                    "SELECT enabled FROM cron_jobs WHERE job_id = ?",
                    (cron_id,),
                ).fetchone()
                if ran_ok:
                    state = "ran"
                elif job is not None:
                    state = "scheduled" if job[0] else "orphaned"
                elif ccon.execute(
                    "SELECT 1 FROM cron_run_logs WHERE job_id = ? LIMIT 1",
                    (cron_id,),
                ).fetchone() is not None:
                    state = "failed"
                else:
                    state = "phantom"
                states[cron_id[:8]] = state

            broken = {k: v for k, v in states.items()
                      if v in ("orphaned", "failed", "phantom")}
            sequence = (payload.get("sequence")
                        or payload.get("sequence_name") or "?")
            enrollments.append({
                "event": event_id, "email": email, "sequence": sequence,
                "age_days": round(age_days, 1), "crons": states,
            })
            if not broken:
                continue
            if age_days > SEQ_ENROLL_MAX_AGE_DAYS:
                broken_stale.append(event_id)
                continue
            broken_now.append(event_id)
            detail = ", ".join(f"{k}={v}" for k, v in states.items())
            alerts.append(alert(
                f"guardian:sequence_integrity:{event_id}",
                f"⚠️ [rick-guardian] SEQUENCE INTEGRITY: enrollment {event_id} "
                f"({email or 'unknown email'}, {sequence}) claims "
                f"{len(cron_ids)} scheduled cron sends but {len(broken)} are "
                f"broken [{detail}]. The sequence will silently never "
                f"complete — cancel it or re-schedule explicitly "
                f"(orphaned=job disabled, phantom=job gone without running).",
            ))
    finally:
        ccon.close()
    return {"cron_enrollments": enrollments, "non_cron_enrollments": non_cron,
            "broken": broken_now, "broken_stale": broken_stale,
            "closed": closed}


# ---------------------------------------------------------------------------
# Check: cron send bypass (ENABLED cron payload embeds a send-API endpoint)
# ---------------------------------------------------------------------------
# Poster pattern mirrored from send-gate-canary.py (its check 5 scans the
# cron store on demand; this is the every-30-min layer). No gate-marker
# exemption: a cron payload cannot call the Python gate, so an embedded send
# endpoint is a raw send path no matter what else the payload says.
CRON_SEND_API_PATTERNS = ("api.resend.com/emails",)

def check_cron_send_bypass(alerts: list) -> dict:
    """No ENABLED OpenClaw cron job may embed a send-API endpoint.

    2026-06-29 an Iris turn created 4 deleteAfterRun crons whose payloads
    curl'd api.resend.com/emails directly — bypassing is_send_allowed,
    suppression lists AND email-sends.jsonl. Two fired unseen (2026-07-02,
    2026-07-06 — the second emailed a customer 2 days after she canceled).
    check_sequence_integrity catches broken sequence *promises*; this
    catches the gate-bypassing send crons themselves. Disabled matches
    (Diane pair 0f85c506/1543d7a7 held for apr_556965a09442, plus the old
    cold-drip cron) are reported as known_disabled without alerting; if
    one is re-enabled it alerts like any other ENABLED match.
    """
    if not OPENCLAW_STATE_DB.exists():
        raise RuntimeError(f"OpenClaw cron store missing: {OPENCLAW_STATE_DB}")
    ccon = sqlite3.connect(f"file:{OPENCLAW_STATE_DB}?mode=ro", uri=True,
                           timeout=5)
    try:
        rows = ccon.execute(
            "SELECT job_id, name, enabled, payload_message, job_json "
            "FROM cron_jobs"
        ).fetchall()
    finally:
        ccon.close()
    enabled_flagged = []
    known_disabled = []
    for job_id, name, enabled, message, job_json in rows:
        blob = ((message or "") + " " + (job_json or "")).lower()
        if not any(p in blob for p in CRON_SEND_API_PATTERNS):
            continue
        entry = {"job_id": job_id, "name": name, "enabled": bool(enabled)}
        if enabled:
            enabled_flagged.append(entry)
            alerts.append(alert(
                f"guardian:cron_send_bypass:{job_id}",
                f"🚨 [rick-guardian] SEND-GATE BYPASS: ENABLED OpenClaw cron "
                f"{job_id} ('{name}') embeds a send-API endpoint in its "
                f"payload — raw curl sends skip is_send_allowed, suppression "
                f"AND email-sends.jsonl (2026-06-29 Iris raw-curl class). "
                f"Disable the job and route it through the gated outbox.",
            ))
        else:
            known_disabled.append(entry)
    return {"jobs_scanned": len(rows), "enabled_flagged": enabled_flagged,
            "known_disabled": known_disabled}


# ---------------------------------------------------------------------------
# Check: nightly heartbeat (pipeline must log completion every night)
# ---------------------------------------------------------------------------
NIGHTLY_LOG = DATA_ROOT / "logs" / "cron" / "nightly.log"
NIGHTLY_MARKER = "Nightly run complete."
NIGHTLY_MAX_AGE_HOURS = 26.0
NIGHTLY_INCOMPLETE_GRACE_HOURS = 2.0

def check_nightly_heartbeat(alerts: list) -> dict:
    """The nightly pipeline must log 'Nightly run complete.' within 26h.

    Jul 14-16 the 03:00 crontab entry silently never fired (Mac asleep at
    03:00; macOS cron never runs missed jobs) — 3 nights of stripe-poll
    revenue truth, retro, brief and snapshot lost with zero alerting. The
    nightly now runs via LaunchAgent ai.rick.nightly (launchd DOES run missed
    StartCalendarInterval jobs on wake); this is the tripwire if that
    regresses.

    run-nightly.sh writes no timestamps, so recency comes from log mtime:
      - mtime older than 26h                    -> nothing ran     -> alert
      - marker on the last non-blank line       -> completed run   -> healthy
      - fresh mtime, no tail marker, >2h quiet  -> started, died   -> alert
      - fresh mtime, no tail marker, <=2h quiet -> likely mid-run  -> ok
    log-rotate.sh truncates keeping the last 2MB in place, so a completed
    run's tail marker survives rotation.
    """
    if not NIGHTLY_LOG.exists():
        alerts.append(alert(
            "guardian:nightly_heartbeat",
            f"🚨 [rick-guardian] NIGHTLY DEAD: {NIGHTLY_LOG} does not exist — "
            f"the nightly pipeline (stripe-poll revenue truth, retro, brief, "
            f"snapshot) is not logging at all. Check 'launchctl list "
            f"ai.rick.nightly'.",
        ))
        return {"state": "log_missing"}
    age_h = (NOW.timestamp() - NIGHTLY_LOG.stat().st_mtime) / 3600.0
    last_line = next(
        (ln for ln in reversed(_tail_lines(NIGHTLY_LOG)) if ln.strip()), "")
    completed_at_tail = NIGHTLY_MARKER in last_line
    if age_h > NIGHTLY_MAX_AGE_HOURS:
        # Whole-night granularity keeps the dedupe hash stable for a day,
        # then escalates naturally each additional missed night.
        alerts.append(alert(
            "guardian:nightly_heartbeat",
            f"🚨 [rick-guardian] NIGHTLY DEAD: no '{NIGHTLY_MARKER}' for "
            f"{int(age_h // 24)} night(s) — nightly.log untouched >26h. "
            f"stripe-poll revenue truth, retro, morning brief and git "
            f"snapshot are NOT running. Check 'launchctl list "
            f"ai.rick.nightly' and the log tail.",
        ))
        return {"state": "stale", "log_age_hours": round(age_h, 1),
                "tail_completed": completed_at_tail}
    if not completed_at_tail and age_h > NIGHTLY_INCOMPLETE_GRACE_HOURS:
        alerts.append(alert(
            "guardian:nightly_heartbeat",
            f"🚨 [rick-guardian] NIGHTLY INCOMPLETE: nightly.log does not end "
            f"with '{NIGHTLY_MARKER}' — the last run started but died "
            f"partway (log quiet >{NIGHTLY_INCOMPLETE_GRACE_HOURS:.0f}h). "
            f"Tail the log for where it stopped.",
        ))
        return {"state": "incomplete", "log_age_hours": round(age_h, 1)}
    return {"state": "healthy" if completed_at_tail else "in_progress",
            "log_age_hours": round(age_h, 1)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        msg = f"🚨 [rick-guardian] SELF-FAILURE: cannot read config {CONFIG_FILE}: {exc}"
        print(msg, file=sys.stderr)
        try:
            alert("guardian:self_error", msg, window_hours=6)
        except Exception:
            pass
        log({"event": "self_error", "error": str(exc)[:300]})
        return 2

    checks = [
        ("session_cost", lambda a: check_session_cost(cfg, a)),
        ("sender", check_sender),
        ("stripe_webhook", lambda a: check_stripe_webhook(cfg, a)),
        ("stripe_provenance", lambda a: check_stripe_provenance(cfg, a)),
        ("zombies", lambda a: check_zombies(cfg, a)),
        ("churn", check_churn),
        ("approval_integrity", check_approval_integrity),
        ("sequence_integrity", check_sequence_integrity),
        ("cron_send_bypass", check_cron_send_bypass),
        ("nightly_heartbeat", check_nightly_heartbeat),
    ]
    alerts: list[str] = []
    results: dict = {}
    errors: list[str] = []
    for name, fn in checks:
        try:
            results[name] = fn(alerts)
        except Exception as exc:
            errors.append(name)
            results[name] = {"error": str(exc)[:300]}
            alerts.append(alert(
                "guardian:self_error",
                f"🚨 [rick-guardian] check '{name}' crashed: {str(exc)[:200]}",
                window_hours=6,
            ))

    fired = [a for a in alerts if a in ("sent", "fallback_file")]
    log({"event": "run", "results": results, "alerts": alerts,
         "errors": errors})

    for name, _ in checks:
        print(f"[{TS}] {name}: {json.dumps(results[name], default=str)[:300]}")
    print(f"[{TS}] alerts fired={len(fired)} suppressed="
          f"{alerts.count('suppressed')} check_errors={len(errors)}")

    if errors:
        return 2
    return 1 if fired else 0


if __name__ == "__main__":
    sys.exit(main())
