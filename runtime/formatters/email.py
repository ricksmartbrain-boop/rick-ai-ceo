"""Email formatter — wraps the existing email-sequence-send pipeline.

For one-off outbound_jobs with channel='email', we write a draft .md file
into ~/rick-vault/mailbox/outbox/ad-hoc/ with YAML frontmatter and let the
standard email-sequence-send.py cron pick it up. That way we use the
battle-tested Resend integration + suppression list + sent-folder move,
and the outbound_jobs row just reports "queued for standard drip pipe".
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.outbound_dispatcher import PermanentError, TransientError

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OUTBOX_DIR = DATA_ROOT / "mailbox" / "outbox" / "ad-hoc"
FILENAME_FMT = "%Y%m%d-%H%M%S"


def send(payload: dict[str, Any]) -> dict[str, Any]:
    """payload: {to, subject, body_md, from? }"""
    to = (payload.get("to") or "").strip()
    subject = (payload.get("subject") or "").strip()
    body_md = (payload.get("body_md") or payload.get("body") or "").strip()
    from_addr = payload.get("from") or os.getenv("MEETRICK_FROM_EMAIL") or "Rick <hello@meetrick.ai>"
    if not to or "@" not in to:
        raise PermanentError(f"invalid recipient: {to!r}")
    if not subject:
        raise PermanentError("subject missing")
    if not body_md:
        raise PermanentError("body_md missing")
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    slug_source = to.lower().encode("utf-8") + subject.lower().encode("utf-8")
    slug = hashlib.sha1(slug_source).hexdigest()[:8]
    stamp = datetime.now().strftime(FILENAME_FMT)
    path = OUTBOX_DIR / f"{stamp}-{slug}-step1.md"
    content = (
        "---\n"
        f"to: {to}\n"
        f"subject: {subject}\n"
        f"from: {from_addr}\n"
        "---\n\n"
        f"{body_md}\n"
    )
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise TransientError(f"mailbox write failed: {exc}") from exc
    return {"status": "queued_for_send", "draft_path": str(path)}
