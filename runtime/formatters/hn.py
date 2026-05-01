"""HN formatter for observe-mode queueing.

The daily proof pipeline uses this as an internal-only channel variant. In
observe mode we do not post externally; we log the payload and return a
terminal observed-only result so the dispatcher marks the job done without
trying to publish.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


LOG_FILE = Path.home() / "rick-vault" / "operations" / "formatter-hn.jsonl"


def _log(event: dict[str, Any]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def send(payload: dict[str, Any]) -> dict[str, Any]:
    content = (payload.get("content") or payload.get("body") or "").strip()
    _log({"ran_at": datetime.now().isoformat(timespec="seconds"), "content_preview": content[:240]})
    return {"status": "observed-only", "reason": "hn observe-mode", "content_chars": len(content)}
