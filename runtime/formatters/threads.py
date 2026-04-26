"""Threads formatter — wraps scripts/post-threads-cdp.py (or -oidc).

Gated by RICK_OUTBOUND_THREADS_LIVE=1. Scaffold logs payload until flipped.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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


def _diag(result: subprocess.CompletedProcess[str]) -> str:
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    return stderr or stdout


def _is_cdp_infra_error(diag: str) -> bool:
    low = diag.lower()
    return (
        "no threads tab" in low
        or "chrome unavailable" in low
        or "chrome not available" in low
        or "chrome" in low and "9222" in low
        or "cdp" in low
    )


def _run_threads_oidc(video_path: str, image_path: str, caption: str) -> subprocess.CompletedProcess[str]:
    if not OIDC_SCRIPT.exists():
        raise PermanentError("no threads OIDC script available")
    cmd = [sys.executable, str(OIDC_SCRIPT), "--caption", caption]
    if video_path:
        cmd.extend(["--video", video_path])
    elif image_path:
        _log({"event": "threads_oidc_text_only_fallback", "reason": "OIDC script has no image upload path", "image_path": image_path})
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)


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

    # Try CDP first; fall back to OIDC only for infra failures.
    cdp_error = ""
    if CDP_SCRIPT.exists() and media_path:
        cdp_cmd = [sys.executable, str(CDP_SCRIPT), media_path, caption]
        try:
            result = subprocess.run(cdp_cmd, capture_output=True, text=True, timeout=180, check=False)
        except subprocess.TimeoutExpired as exc:
            cdp_error = f"threads timeout: {exc}"
        else:
            diag = _diag(result)
            low = diag.lower()
            if result.returncode == 0:
                return {"status": "sent", "stdout": (result.stdout or "").strip()[:500]}
            cdp_error = diag or f"threads failed rc={result.returncode}"
            if "401" in diag or "login" in low or "unauthorized" in low:
                raise AuthFailure(f"threads auth: {diag[:500]}")
            if "429" in diag or "rate" in low:
                raise TransientError(f"threads rate: {diag[:500]}")
            if not _is_cdp_infra_error(diag):
                raise TransientError(f"threads failed: {diag[:500]}")
    elif CDP_SCRIPT.exists() and not media_path:
        cdp_error = "missing media for CDP; using OIDC text-only fallback"
    else:
        cdp_error = "CDP script missing"

    oidc_result = _run_threads_oidc(video_path, image_path, caption)
    oidc_diag = _diag(oidc_result)
    if oidc_result.returncode != 0:
        raise TransientError(f"threads oidc failed after cdp fallback: cdp={cdp_error[:300]} | oidc={oidc_diag[:300]}")
    return {"status": "sent", "stdout": (oidc_result.stdout or "").strip()[:500]}
