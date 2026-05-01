"""Cold email subject formatter for observe-mode queueing."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


LOG_FILE = Path.home() / "rick-vault" / "operations" / "formatter-cold-email-subject.jsonl"


def _log(event: dict[str, Any]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def send(payload: dict[str, Any]) -> dict[str, Any]:
    subject = (payload.get("subject") or payload.get("content") or payload.get("body") or "").strip()
    _log({"ran_at": datetime.now().isoformat(timespec="seconds"), "subject": subject[:240]})
    return {"status": "observed-only", "reason": "cold email subject observe-mode", "subject_chars": len(subject)}
