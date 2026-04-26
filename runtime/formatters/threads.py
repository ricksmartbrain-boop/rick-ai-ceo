"""Threads formatter — wraps scripts/post-threads-cdp.py (or -oidc).

Gated by RICK_OUTBOUND_THREADS_LIVE=1. Scaffold logs payload until flipped.
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
CDP_SCRIPT = SCRIPTS_DIR / "post-threads-cdp.py"
OIDC_SCRIPT = SCRIPTS_DIR / "post-threads-oidc.py"
LOG_FILE = Path.home() / "rick-vault" / "operations" / "formatter-threads.jsonl"


def _log(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def send(payload: dict[str, Any]) -> dict[str, Any]:
    caption = (payload.get("caption") or payload.get("body") or payload.get("content") or "").strip()
    caption = stamp_urls_in_text(caption, "threads", payload.get("lane"), payload.get("msg_id"))
    video_path = (payload.get("video_path") or "").strip()
    image_path = (payload.get("image_path") or "").strip()
    media_path = video_path or image_path
    if not caption:
        raise PermanentError("caption required")

    live = os.getenv("RICK_OUTBOUND_THREADS_LIVE") == "1"
    _log(
        {
            "ran_at": datetime.now().isoformat(timespec="seconds"),
            "live": live,
            "caption_preview": caption[:200],
            "has_video": bool(video_path),
            "has_image": bool(image_path),
            "media_path": media_path or None,
        }
    )
    if not live:
        return {"status": "observed-only", "reason": "RICK_OUTBOUND_THREADS_LIVE!=1"}

    # Prefer CDP when available (uses the already-running Chrome session).
    if CDP_SCRIPT.exists():
        if not media_path:
            raise PermanentError("media file required for Threads CDP (video_path or image_path missing from payload)")
        cmd = ["python3", str(CDP_SCRIPT), media_path, caption]
    elif OIDC_SCRIPT.exists():
        cmd = ["python3", str(OIDC_SCRIPT), "--caption", caption]
        if video_path:
            cmd.extend(["--video", video_path])
    else:
        raise PermanentError("no threads script available")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TransientError(f"threads timeout: {exc}") from exc
    stderr = (result.stderr or "")[:500]
    low = stderr.lower()
    if result.returncode != 0:
        if "401" in stderr or "login" in low or "unauthorized" in low:
            raise AuthFailure(f"threads auth: {stderr}")
        if "429" in stderr or "rate" in low:
            raise TransientError(f"threads rate: {stderr}")
        raise TransientError(f"threads failed: {stderr}")
    return {"status": "sent", "stdout": (result.stdout or "")[:500]}
