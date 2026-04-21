"""Reddit formatter — wraps scripts/post-reddit-api.py for posts,
scripts/post-reddit-cdp.py for comments.

Gated by RICK_OUTBOUND_REDDIT_LIVE=1. Scaffold logs payload + returns
observed-only until flipped.
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
POST_SCRIPT = SCRIPTS_DIR / "post-reddit-api.py"
COMMENT_SCRIPT = SCRIPTS_DIR / "post-reddit-cdp.py"
LOG_FILE = Path.home() / "rick-vault" / "operations" / "formatter-reddit.jsonl"


def _log(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def send(payload: dict[str, Any]) -> dict[str, Any]:
    kind = (payload.get("kind") or "post").lower()  # post|comment
    subreddit = (payload.get("subreddit") or "").strip()
    title = (payload.get("title") or "").strip()
    body = (payload.get("body") or payload.get("content") or "").strip()
    body = stamp_urls_in_text(body, "reddit", payload.get("lane"), payload.get("msg_id"))
    target_url = (payload.get("target_url") or "").strip()

    if kind == "post":
        if not subreddit or not title:
            raise PermanentError("subreddit + title required for post")
    elif kind == "comment":
        if not target_url or not body:
            raise PermanentError("target_url + body required for comment")
    else:
        raise PermanentError(f"unknown reddit kind: {kind}")

    live = os.getenv("RICK_OUTBOUND_REDDIT_LIVE") == "1"
    _log(
        {
            "ran_at": datetime.now().isoformat(timespec="seconds"),
            "live": live,
            "kind": kind,
            "subreddit": subreddit,
            "title": title,
            "body_preview": body[:200],
        }
    )
    if not live:
        return {"status": "observed-only", "reason": "RICK_OUTBOUND_REDDIT_LIVE!=1"}

    if kind == "post":
        script = POST_SCRIPT
        cmd = ["python3", str(script), "--subreddit", subreddit, "--title", title, "--body", body]
    else:
        script = COMMENT_SCRIPT
        cmd = ["python3", str(script), "--url", target_url, "--body", body]

    if not script.exists():
        raise PermanentError(f"script missing: {script}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TransientError(f"reddit timeout: {exc}") from exc
    stderr = (result.stderr or "")[:500]
    if result.returncode != 0:
        low = stderr.lower()
        if "401" in stderr or "403" in stderr or "forbidden" in low or "login required" in low:
            raise AuthFailure(f"reddit auth: {stderr}")
        if "shadowban" in low or "blocked" in low:
            raise AuthFailure(f"reddit shadowban: {stderr}")
        if "429" in stderr or "rate" in low:
            raise TransientError(f"reddit rate: {stderr}")
        raise TransientError(f"reddit failed: {stderr}")
    return {"status": "sent", "stdout": (result.stdout or "")[:500]}
