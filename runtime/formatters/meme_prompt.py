"""Meme prompt formatter for observe-mode queueing."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


LOG_FILE = Path.home() / "rick-vault" / "operations" / "formatter-meme-prompt.jsonl"


def _log(event: dict[str, Any]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def send(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = (payload.get("prompt") or payload.get("content") or payload.get("body") or "").strip()
    _log({"ran_at": datetime.now().isoformat(timespec="seconds"), "prompt_preview": prompt[:240]})
    return {"status": "observed-only", "reason": "meme prompt observe-mode", "prompt_chars": len(prompt)}
