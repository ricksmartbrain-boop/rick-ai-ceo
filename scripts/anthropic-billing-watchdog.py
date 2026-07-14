#!/usr/bin/env python3
"""Anthropic Billing Watchdog — self-healing layer for gateway auth-profile state

Runs every 15 min via launchd (ai.rick.anthropic-billing-watchdog.plist).

Auth state lives in SQLite (openclaw-agent.sqlite, table auth_profile_state,
state_key='primary') since the 2026 migration off auth-state.json.

Each tick:
  1. Probe Anthropic API directly (claude-opus-4-8, max_tokens=10)
  2. If 200 OK AND auth state has disabledUntil set → clear it + restart gateway
  3. If HTTP 400 / credit balance too low → leave disable intact, log warning,
     and Telegram-alert ops-alerts on transition (prev probe OK → failing) plus
     once per 24h while it persists
  4. Any other status → log error, leave state alone

Invariants enforced:
  - Smart-models only: probes with claude-opus-4-8 (fallback: claude-sonnet-4-6).
    Never mini/nano.
  - Credits direction: ONLY clears disables. Never creates them.
  - Idempotent: if already clean, just log "ok" and exit.

Log: ~/rick-vault/operations/billing-watchdog.jsonl
     (sourced by flag_health.py RICK_BILLING_WATCHDOG_LIVE probe)
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
AUTH_DB_FILE = Path(
    "/Users/rickthebot/.openclaw/agents/main/agent/openclaw-agent.sqlite"
)
AUTH_STATE_KEY = "primary"
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LOG_FILE = DATA_ROOT / "operations" / "billing-watchdog.jsonl"

ANTHROPIC_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
PROBE_URL = "https://api.anthropic.com/v1/messages"

# Smart-models invariant: test with opus first, fallback to sonnet. Never mini.
PROBE_MODELS = ["claude-opus-4-8", "claude-sonnet-4-6"]

# Provider key as it appears in the auth-profile state usageStats
PROVIDER_KEY = "anthropic:default"

# Telegram alerting (tg-topic.sh convention: ops-alerts topic in team chat)
RICK_ENV_FILES = [
    Path.home() / ".openclaw/workspace/config/rick.env",
    Path.home() / "clawd/config/rick.env",
]
TEAM_CHAT_ID_DEFAULT = "-1003781085932"
OPS_ALERTS_THREAD_ID = "34"
ALERT_EVENT = "credits_low_alert_sent"
PROBE_STATUSES = ("ok", "cleared", "credits_low", "error")
ALERT_REPEAT_SECONDS = 24 * 3600

TS = datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(entry: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry.setdefault("ts", TS)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Auth-state helpers (SQLite: auth_profile_state, state_key='primary')
# ---------------------------------------------------------------------------
def read_auth_state() -> dict:
    if not AUTH_DB_FILE.exists():
        return {}
    try:
        con = sqlite3.connect(AUTH_DB_FILE, timeout=5)
        try:
            row = con.execute(
                "SELECT state_json FROM auth_profile_state WHERE state_key = ?",
                (AUTH_STATE_KEY,),
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return {}
        return json.loads(row[0])
    except Exception as exc:
        log({"event": "auth_state_read_error", "status": "error", "error": str(exc)})
        return {}


def write_auth_state(state: dict) -> None:
    """Targeted UPDATE of the single primary row; one sub-second transaction.

    WAL mode on the gateway DB tolerates this concurrent writer."""
    payload = json.dumps(state, separators=(",", ":"))
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    con = sqlite3.connect(AUTH_DB_FILE, timeout=5)
    try:
        with con:
            cur = con.execute(
                "UPDATE auth_profile_state SET state_json = ?, updated_at = ? "
                "WHERE state_key = ?",
                (payload, now_ms, AUTH_STATE_KEY),
            )
            if cur.rowcount != 1:
                raise RuntimeError(
                    f"auth_profile_state UPDATE touched {cur.rowcount} rows "
                    f"(expected 1) — aborted"
                )
    finally:
        con.close()


def get_provider_entry(state: dict) -> dict:
    """Return the mutable usageStats entry for anthropic:default."""
    return state.get("usageStats", {}).get(PROVIDER_KEY, {})


def has_disable(state: dict) -> tuple[bool, str | None]:
    """Return (is_disabled, disabled_until_value_str)."""
    entry = get_provider_entry(state)
    du = entry.get("disabledUntil")
    if du is not None:
        return True, str(du)
    return False, None


def clear_disabled_until(state: dict) -> dict:
    """Remove disabledUntil / disabledReason / failureCounts from provider entry.
    Never touches any other provider. Returns modified state (in-place)."""
    usage = state.get("usageStats", {})
    entry = usage.get(PROVIDER_KEY, {})
    for field in ("disabledUntil", "disabledReason", "failureCounts"):
        entry.pop(field, None)
    # Reset errorCount so gateway doesn't immediately re-disable on first use
    entry["errorCount"] = 0
    usage[PROVIDER_KEY] = entry
    state["usageStats"] = usage
    return state


# ---------------------------------------------------------------------------
# API probe
# ---------------------------------------------------------------------------
def probe_anthropic() -> tuple[int, dict]:
    """Direct POST to Anthropic messages API.

    Tries PROBE_MODELS in order; returns on first non-429 response.
    Returns (http_status, parsed_body).
    """
    if not ANTHROPIC_KEY:
        return 0, {"error": "ANTHROPIC_API_KEY not set"}

    for model in PROBE_MODELS:
        payload = json.dumps({
            "model": model,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "ping"}],
        }).encode()

        req = urllib.request.Request(
            PROBE_URL,
            data=payload,
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read())
                return resp.status, body
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read())
            except Exception:
                body = {"raw_error": str(exc)}
            code = exc.code
            if code == 429:
                # Rate-limit: try next model
                continue
            return code, body
        except Exception as exc:
            return 0, {"error": str(exc)}

    # All models 429'd
    return 429, {"error": "all probe models rate-limited"}


# ---------------------------------------------------------------------------
# Telegram alerting
# ---------------------------------------------------------------------------
def _rick_env(name: str) -> str:
    """Env var, falling back to rick.env files (never logs values)."""
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
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return ""


def send_telegram_alert(text: str) -> tuple[bool, str]:
    """POST to ops-alerts topic. Returns (ok, message_id_or_error)."""
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


def read_alert_context() -> tuple[str | None, float | None]:
    """Scan the jsonl for (last probe status, last alert epoch seconds)."""
    last_probe: str | None = None
    last_alert: float | None = None
    if not LOG_FILE.exists():
        return None, None
    try:
        for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except Exception:
                continue
            ts_raw = entry.get("ts", "")
            if entry.get("event") == ALERT_EVENT:
                try:
                    last_alert = datetime.fromisoformat(ts_raw).timestamp()
                except Exception:
                    pass
            elif entry.get("status") in PROBE_STATUSES:
                last_probe = entry["status"]
    except Exception:
        return None, None
    return last_probe, last_alert


# ---------------------------------------------------------------------------
# Gateway restart
# ---------------------------------------------------------------------------
def restart_gateway() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["openclaw", "gateway", "restart"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = (result.stdout + result.stderr).strip()
        return result.returncode == 0, out[:300]
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    status_code, resp_body = probe_anthropic()
    state = read_auth_state()
    disabled, disabled_until = has_disable(state)

    # ------------------------------------------------------------------
    # Case 1: API is live (200 OK)
    # ------------------------------------------------------------------
    if status_code == 200:
        if disabled:
            old_until = disabled_until
            state = clear_disabled_until(state)
            write_auth_state(state)
            ok, gw_out = restart_gateway()
            log({
                "event": "disabled_cleared",
                "status": "cleared",
                "api_status": status_code,
                "model_probed": PROBE_MODELS[0],
                "cleared_disabled_until": old_until,
                "gateway_restart_ok": ok,
                "gateway_output": gw_out,
            })
            print(
                f"[{TS}] ✅ Cleared stale disabledUntil={old_until} "
                f"| gateway restart: {'OK' if ok else 'FAILED'}"
            )
        else:
            log({
                "event": "probe_ok",
                "status": "ok",
                "api_status": status_code,
                "model_probed": PROBE_MODELS[0],
                "disabled_in_state": False,
            })
            print(f"[{TS}] ✅ API OK — no stale disable in place")
        return 0

    # ------------------------------------------------------------------
    # Case 2: Credits low (400 / 402 / 529 with billing message)
    # ------------------------------------------------------------------
    err_obj = resp_body.get("error", {})
    if not isinstance(err_obj, dict):
        err_obj = {}
    error_msg = str(err_obj.get("message", resp_body)).lower()

    credit_keywords = ("credit balance", "insufficient", "billing", "payment")
    is_credit_low = status_code in (400, 402, 529) and any(
        k in error_msg for k in credit_keywords
    )

    if is_credit_low:
        prev_status, last_alert_ts = read_alert_context()
        log({
            "event": "credits_low",
            "status": "credits_low",
            "api_status": status_code,
            "model_probed": PROBE_MODELS[0],
            "error": error_msg[:300],
            "disable_left_intact": disabled,
        })

        # Alert on OK→failing transition, then once per 24h while it persists
        now_epoch = datetime.now(timezone.utc).timestamp()
        transition = prev_status in ("ok", "cleared")
        repeat_due = (
            last_alert_ts is None
            or now_epoch - last_alert_ts >= ALERT_REPEAT_SECONDS
        )
        if transition or repeat_due:
            alert_kind = "transition" if transition else "daily_repeat"
            tg_ok, tg_detail = send_telegram_alert(
                f"🚨 [billing-watchdog] Anthropic credits LOW "
                f"(HTTP {status_code}, probe {PROBE_MODELS[0]}). "
                f"Brain is on fallback model. Manual top-up needed "
                f"(Anthropic Plans & Billing). "
                f"Alert kind: {alert_kind}."
            )
            log({
                "event": ALERT_EVENT,
                "status": "credits_low",
                "alert_kind": alert_kind,
                "telegram_ok": tg_ok,
                "telegram_detail": tg_detail,
            })
            print(
                f"[{TS}] 📣 Telegram alert ({alert_kind}): "
                f"{'sent, message_id=' + tg_detail if tg_ok else 'FAILED: ' + tg_detail}"
            )

        print(
            f"[{TS}] ⚠️  Credits low (HTTP {status_code}) "
            f"— disable left intact: {disabled}"
        )
        return 1

    # ------------------------------------------------------------------
    # Case 3: Other error (network, bad key, unexpected status)
    # ------------------------------------------------------------------
    log({
        "event": "probe_error",
        "status": "error",
        "api_status": status_code,
        "model_probed": PROBE_MODELS[0],
        "error": error_msg[:300] or str(resp_body)[:300],
    })
    print(f"[{TS}] ❌ Probe error HTTP {status_code}: {error_msg[:120]}")
    return 2


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test-alert":
        msg = sys.argv[2] if len(sys.argv) > 2 else "[billing-watchdog] test alert"
        ok, detail = send_telegram_alert(msg)
        print(f"test-alert: {'OK message_id=' + detail if ok else 'FAILED: ' + detail}")
        sys.exit(0 if ok else 2)
    sys.exit(main())
