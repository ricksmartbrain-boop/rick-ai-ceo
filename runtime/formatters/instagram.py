"""Instagram formatter — wraps scripts/post-instagram-reel-cdp.py or
scripts/post-instagram-playwright.py.

Gated by RICK_OUTBOUND_INSTAGRAM_LIVE=1. Scaffold logs payload until flipped.
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
REEL_CDP_SCRIPT = SCRIPTS_DIR / "post-instagram-reel-cdp.py"
PW_SCRIPT = SCRIPTS_DIR / "post-instagram-playwright.py"
LOG_FILE = Path.home() / "rick-vault" / "operations" / "formatter-instagram.jsonl"


def _log(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def send(payload: dict[str, Any]) -> dict[str, Any]:
    caption = (payload.get("caption") or payload.get("body") or "").strip()
    caption = stamp_urls_in_text(caption, "instagram", payload.get("lane"), payload.get("msg_id"))
    video_path = (payload.get("video_path") or "").strip()
    image_path = (payload.get("image_path") or "").strip()
    if not caption:
        raise PermanentError("caption required")
    if not (video_path or image_path):
        raise PermanentError("video_path or image_path required (Instagram needs media)")

    live = os.getenv("RICK_OUTBOUND_INSTAGRAM_LIVE") == "1"
    _log(
        {
            "ran_at": datetime.now().isoformat(timespec="seconds"),
            "live": live,
            "caption_preview": caption[:200],
            "media": video_path or image_path,
        }
    )
    if not live:
        return {"status": "observed-only", "reason": "RICK_OUTBOUND_INSTAGRAM_LIVE!=1"}

    if video_path and REEL_CDP_SCRIPT.exists():
        cmd = ["python3", str(REEL_CDP_SCRIPT), video_path, caption]
    elif PW_SCRIPT.exists():
        cmd = ["python3", str(PW_SCRIPT), "--caption", caption]
        if video_path:
            cmd.extend(["--video", video_path])
        elif image_path:
            cmd.extend(["--image", image_path])
    else:
        raise PermanentError("no instagram script available")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=240, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TransientError(f"instagram timeout: {exc}") from exc
    stderr = (result.stderr or "")[:500]
    low = stderr.lower()
    if result.returncode != 0:
        if "401" in stderr or "login" in low or "unauthorized" in low:
            raise AuthFailure(f"instagram auth: {stderr}")
        if "429" in stderr or "rate" in low:
            raise TransientError(f"instagram rate: {stderr}")
        raise TransientError(f"instagram failed: {stderr}")
    return {"status": "sent", "stdout": (result.stdout or "")[:500]}
