"""Moltbook formatter — wraps scripts/moltbook-post.py.

Two sub-channels share this module:
  - channel='moltbook'       → DM payload (submolt='dm', target_user, content)
  - channel='moltbook_post'  → public post (submolt=subreddit-like, title, content)

Gated by RICK_OUTBOUND_MOLTBOOK_LIVE=1. Without the flag the formatter
logs the payload and returns skipped — this lets outbound_dispatcher drain
the queue safely while the channel is in observation mode.
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

SCRIPT = Path.home() / "clawd" / "scripts" / "moltbook-post.py"
LOG_FILE = Path.home() / "rick-vault" / "operations" / "formatter-moltbook.jsonl"


def _log(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def send(payload: dict[str, Any]) -> dict[str, Any]:
    submolt = (payload.get("submolt") or payload.get("subreddit") or "").strip()
    title = (payload.get("title") or "").strip()
    content = (payload.get("content") or payload.get("body") or "").strip()
    content = stamp_urls_in_text(content, "moltbook", payload.get("lane"), payload.get("msg_id"))
    if not submolt or not content:
        raise PermanentError("submolt + content required")
    if not SCRIPT.exists():
        raise PermanentError(f"script missing: {SCRIPT}")

    live = os.getenv("RICK_OUTBOUND_MOLTBOOK_LIVE") == "1"
    _log(
        {
            "ran_at": datetime.now().isoformat(timespec="seconds"),
            "live": live,
            "submolt": submolt,
            "title": title,
            "content_preview": content[:200],
        }
    )
    if not live:
        return {"status": "observed-only", "reason": "RICK_OUTBOUND_MOLTBOOK_LIVE!=1"}

    cmd = ["python3", str(SCRIPT), "--submolt", submolt, "--content", content]
    if title:
        cmd.extend(["--title", title])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TransientError(f"moltbook-post.py timeout: {exc}") from exc
    stderr = (result.stderr or "")[:500]
    stdout = (result.stdout or "")[:500]
    if result.returncode != 0:
        if "401" in stderr or "Unauthorized" in stderr or "invalid api key" in stderr.lower():
            raise AuthFailure(f"moltbook auth: {stderr}")
        if "429" in stderr or "rate" in stderr.lower():
            raise TransientError(f"moltbook rate-limited: {stderr}")
        raise TransientError(f"moltbook failed rc={result.returncode}: {stderr}")
    return {"status": "sent", "stdout": stdout}
