"""Email formatter — wraps the existing email-sequence-send pipeline.

For one-off outbound_jobs with channel='email', we write a draft .md file
into ~/rick-vault/mailbox/outbox/ad-hoc/ with YAML frontmatter and let the
standard email-sequence-send.py cron pick it up. That way we use the
battle-tested Resend integration + suppression list + sent-folder move,
and the outbound_jobs row just reports "queued for standard drip pipe".

SUPPRESSION GUARD (defense-in-depth):
  Before writing to outbox, this formatter checks suppression.txt so bounced /
  unsubscribed addresses are blocked at queue-time, not just at drain-time.
  Violations are logged to ~/rick-vault/operations/suppression-violations.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.outbound_dispatcher import PermanentError, TransientError
from runtime.utm import stamp_urls_in_text

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OUTBOX_DIR = DATA_ROOT / "mailbox" / "outbox" / "ad-hoc"
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"
VIOLATIONS_FILE = DATA_ROOT / "operations" / "suppression-violations.jsonl"
FILENAME_FMT = "%Y%m%d-%H%M%S"
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_FAKE_TLDS = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "mp4", "pdf", "csv",
    "json", "md", "html", "css", "js", "ts", "txt", "xml", "zip",
}


def _is_real_email(addr: str) -> bool:
    if not _EMAIL_RE.match(addr):
        return False
    tld = addr.rsplit(".", 1)[-1].lower()
    return tld not in _FAKE_TLDS


# Module-level suppression cache (refreshed on each call for safety).
_suppression_cache: dict[str, str] | None = None
_suppression_mtime: float = 0.0


def _load_suppressed() -> dict[str, str]:
    """Return {email_lower: reason} from suppression.txt. Cached per mtime."""
    global _suppression_cache, _suppression_mtime
    try:
        mtime = SUPPRESSION_FILE.stat().st_mtime if SUPPRESSION_FILE.exists() else 0.0
    except OSError:
        mtime = 0.0
    if _suppression_cache is not None and mtime == _suppression_mtime:
        return _suppression_cache
    result: dict[str, str] = {}
    if SUPPRESSION_FILE.exists():
        for raw in SUPPRESSION_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("#", 1)
            email = parts[0].strip().lower()
            reason = parts[1].strip() if len(parts) > 1 else "suppressed"
            if _is_real_email(email):
                result[email] = reason
    _suppression_cache = result
    _suppression_mtime = mtime
    return result


def _log_suppression_violation(to: str, reason: str, payload: dict) -> None:
    """Append a violation entry and attempt an operator alert (non-fatal)."""
    try:
        VIOLATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "violation": "formatter_blocked_suppressed_recipient",
            "to": to,
            "suppression_reason": reason,
            "subject": payload.get("subject", ""),
        }
        with VIOLATIONS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass
    try:
        import sqlite3
        db_path = DATA_ROOT / "runtime" / "rick-runtime.db"
        if not db_path.exists():
            import pathlib
            db_path = pathlib.Path.home() / ".openclaw" / "workspace" / "runtime" / "rick-runtime.db"
        if db_path.exists():
            import sys
            sys.path.insert(0, str(Path.home() / ".openclaw" / "workspace"))
            from runtime.engine import notify_operator_deduped
            conn = sqlite3.connect(str(db_path))
            msg = (
                f"🚨 SUPPRESSION VIOLATION (formatter): attempted send to suppressed address "
                f"{to!r} (reason: {reason}). Blocked at queue-time. "
                f"Details: {VIOLATIONS_FILE}"
            )
            notify_operator_deduped(conn, msg, kind="suppression_violation_formatter",
                                    dedup_window_hours=6)
            conn.close()
    except Exception:
        pass  # alert failure must never crash the formatter


def send(payload: dict[str, Any]) -> dict[str, Any]:
    """payload: {to, subject, body_md, from? }"""
    to = (payload.get("to") or "").strip()
    subject = (payload.get("subject") or "").strip()
    body_md = (payload.get("body_md") or payload.get("body") or "").strip()
    # Stamp meetrick.ai URLs with UTMs for attribution (no-op on non-meetrick URLs).
    body_md = stamp_urls_in_text(body_md, "email", payload.get("lane"), payload.get("msg_id"))
    from_addr = payload.get("from") or os.getenv("MEETRICK_FROM_EMAIL") or "Rick <hello@meetrick.ai>"
    if not to or "@" not in to:
        raise PermanentError(f"invalid recipient: {to!r}")
    if not subject:
        raise PermanentError("subject missing")
    if not body_md:
        raise PermanentError("body_md missing")
    # ── suppression guard (defense-in-depth) ──────────────────────────────────
    suppressed = _load_suppressed()
    if to.lower() in suppressed:
        reason = suppressed[to.lower()]
        _log_suppression_violation(to, reason, payload)
        raise PermanentError(
            f"recipient {to!r} is suppressed (reason: {reason}); "
            "blocked at formatter layer"
        )
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
