#!/usr/bin/env python3
"""Anthropic Billing Watchdog — self-healing layer for gateway auth-state.json

Runs every 15 min via launchd (ai.rick.anthropic-billing-watchdog.plist).

Each tick:
  1. Probe Anthropic API directly (claude-opus-4-7, max_tokens=10)
  2. If 200 OK AND auth-state has disabledUntil set → clear it + restart gateway
  3. If HTTP 400 / credit balance too low → leave disable intact, log warning
  4. Any other status → log error, leave state alone

Invariants enforced:
  - Smart-models only: probes with claude-opus-4-7 (fallback: claude-sonnet-4-6).
    Never mini/nano.
  - Credits direction: ONLY clears disables. Never creates them.
  - Idempotent: if already clean, just log "ok" and exit.

Log: ~/rick-vault/operations/billing-watchdog.jsonl
     (sourced by flag_health.py RICK_BILLING_WATCHDOG_LIVE probe)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
AUTH_STATE_FILE = Path(
    "/Users/rickthebot/.openclaw/agents/main/agent/auth-state.json"
)
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LOG_FILE = DATA_ROOT / "operations" / "billing-watchdog.jsonl"

ANTHROPIC_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
PROBE_URL = "https://api.anthropic.com/v1/messages"

# Smart-models invariant: test with opus first, fallback to sonnet. Never mini.
PROBE_MODELS = ["claude-opus-4-7", "claude-sonnet-4-6"]

# Provider key as it appears in auth-state.json usageStats
PROVIDER_KEY = "anthropic:default"

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
# Auth-state helpers
# ---------------------------------------------------------------------------
def read_auth_state() -> dict:
    if not AUTH_STATE_FILE.exists():
        return {}
    try:
        return json.loads(AUTH_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log({"event": "auth_state_read_error", "status": "error", "error": str(exc)})
        return {}


def write_auth_state(state: dict) -> None:
    tmp = AUTH_STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(AUTH_STATE_FILE)


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
        log({
            "event": "credits_low",
            "status": "credits_low",
            "api_status": status_code,
            "model_probed": PROBE_MODELS[0],
            "error": error_msg[:300],
            "disable_left_intact": disabled,
        })
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
    sys.exit(main())
