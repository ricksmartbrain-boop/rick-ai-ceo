"""LinkedIn formatter — wraps scripts/post-linkedin*.py / DM scripts.

Two sub-channels:
  - channel='linkedin'       → DM (requires target_url or profile_handle)
  - channel='linkedin_post'  → founder-voice post

Gated by RICK_OUTBOUND_LINKEDIN_LIVE=1. Session via chrome-cdp-linkedin
LaunchAgent (already running). 401/login-wall → AuthFailure so
kill_switches auto-pauses the channel for 24h.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.outbound_dispatcher import AuthFailure, PermanentError, TransientError
from runtime.utm import stamp_urls_in_text

SCRIPTS_DIR = Path.home() / "clawd" / "scripts"
# DM + invite → new linkedin-dm-cdp.js (2026-04-22 sprint)
DM_SCRIPT = SCRIPTS_DIR / "linkedin-dm-cdp.js"
# Post script: linkedin-post-v4.js accepts --port and --body args.
POST_SCRIPT = Path.home() / "clawd" / "scripts" / "linkedin-post-v4.js"
DEFAULT_PORT = int(os.getenv("RICK_LINKEDIN_CDP_PORT", "9225"))
LOG_FILE = Path.home() / "rick-vault" / "operations" / "formatter-linkedin.jsonl"


def _log(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def send(payload: dict[str, Any]) -> dict[str, Any]:
    kind = (payload.get("kind") or "post").lower()
    body = (payload.get("body") or payload.get("content") or "").strip()
    body = stamp_urls_in_text(body, "linkedin", payload.get("lane"), payload.get("msg_id"))
    target = (payload.get("target_url") or payload.get("profile_handle") or "").strip()

    if not body:
        raise PermanentError("body required")
    if kind == "dm" and not target:
        raise PermanentError("target_url or profile_handle required for dm")

    live = os.getenv("RICK_OUTBOUND_LINKEDIN_LIVE") == "1"
    _log(
        {
            "ran_at": datetime.now().isoformat(timespec="seconds"),
            "live": live,
            "kind": kind,
            "target": target[:200],
            "body_preview": body[:200],
        }
    )
    if not live:
        return {"status": "observed-only", "reason": "RICK_OUTBOUND_LINKEDIN_LIVE!=1"}

    # DM + invite both use linkedin-dm-cdp.js; post uses linkedin-post-v3.js
    script = POST_SCRIPT if kind == "post" else DM_SCRIPT
    if not script.exists():
        raise PermanentError(f"script missing: {script}")

    cmd = ["node", str(script)]
    if kind in ("dm", "invite"):
        cmd.extend([
            "--port", str(DEFAULT_PORT),
            "--target", target,
            "--body", body,
            "--kind", kind,
        ])
    else:  # post
        cmd.extend(["--port", str(DEFAULT_PORT), "--body", body])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TransientError(f"linkedin timeout: {exc}") from exc
    stderr = (result.stderr or "")[:500]
    low = stderr.lower()
    if result.returncode != 0:
        if "401" in stderr or "login" in low or "sign in" in low or "unauthorized" in low:
            raise AuthFailure(f"linkedin auth: {stderr}")
        if "captcha" in low or "challenge" in low:
            raise AuthFailure(f"linkedin captcha: {stderr}")
        if "429" in stderr or "rate" in low:
            raise TransientError(f"linkedin rate: {stderr}")
        raise TransientError(f"linkedin failed: {stderr}")
    return {"status": "sent", "stdout": (result.stdout or "")[:500]}
