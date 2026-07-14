"""Gmail personal-touch formatter.

Sends via rick@meetrick.ai SMTP (App Password) — looks like a real human reply
to the lead rather than a bulk Resend transactional send. Used by the
multi-touch sequencer for Day-5 and Day-15 personal-touch slots.

Contract mirrors runtime/formatters/email.py:
  send(payload) → {"status": "sent", ...}  OR raises PermanentError

payload keys:
  to        (required) recipient email
  subject   (required) subject line
  body_md   (or 'body') message body — markdown OK, sent as plain text
  from      (optional) override From address (default: Rick <rick@meetrick.ai>)
  reply_to  (optional) Reply-To header

Auth:
  App Password read from ~/.config/himalaya/app-password (same cred as himalaya).
  Falls back to GMAIL_APP_PASSWORD env var, then GMAIL_SMTP_PASSWORD.

Logging:
  Appends a JSON line to ~/rick-vault/operations/outbound-dispatcher.jsonl so
  the ai.rick.gmail-personal probe in flag_health.py can track liveness.
"""

from __future__ import annotations

import json
import os
import smtplib
import subprocess
import textwrap
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from runtime.outbound_dispatcher import PermanentError, TransientError

# ── constants ────────────────────────────────────────────────────────────────

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
FROM_DEFAULT = "Rick <rick@meetrick.ai>"
FROM_ADDR = "rick@meetrick.ai"
APP_PASS_FILE = Path.home() / ".config" / "himalaya" / "app-password"

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS_LOG = DATA_ROOT / "operations" / "outbound-dispatcher.jsonl"


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_app_password() -> str:
    """Read App Password from file, env var, or error out cleanly."""
    # 1. Himalaya cred file (preferred — same source as IMAP watcher)
    if APP_PASS_FILE.exists():
        try:
            pw = APP_PASS_FILE.read_text(encoding="utf-8").strip()
            if pw:
                return pw
        except OSError:
            pass
    # 2. Env vars
    for var in ("GMAIL_APP_PASSWORD", "GMAIL_SMTP_PASSWORD"):
        pw = os.getenv(var, "").strip()
        if pw:
            return pw
    raise PermanentError(
        "gmail_personal: no app password found — "
        f"expected at {APP_PASS_FILE} or GMAIL_APP_PASSWORD env var"
    )


def _md_to_plain(body_md: str) -> str:
    """Best-effort markdown → plain text (no extra deps required)."""
    import re
    text = body_md
    # Strip markdown links [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Strip inline code backticks
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Strip bold/italic markers
    text = re.sub(r'[*_]{1,3}([^*_]+)[*_]{1,3}', r'\1', text)
    # Strip heading markers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Normalise whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _append_log(entry: dict) -> None:
    OPS_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with OPS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # logging is best-effort; never block send on log failure


# ── public API ───────────────────────────────────────────────────────────────

def send(payload: dict[str, Any]) -> dict[str, Any]:
    """Send a personal-touch email via rick@meetrick.ai Gmail SMTP.

    Returns: {"status": "sent", "to": ..., "subject": ...}
    Raises:  PermanentError for bad input or unrecoverable auth failure
             TransientError for network/timeout issues (caller may retry)
    """
    to = (payload.get("to") or "").strip()
    subject = (payload.get("subject") or "").strip()
    body_raw = (payload.get("body_md") or payload.get("body") or "").strip()
    from_header = (payload.get("from") or "").strip() or FROM_DEFAULT
    reply_to = (payload.get("reply_to") or "").strip()

    # ── validation ────────────────────────────────────────────────────────
    if not to or "@" not in to:
        raise PermanentError(f"gmail_personal: invalid recipient: {to!r}")
    if not subject:
        raise PermanentError("gmail_personal: subject missing")
    if not body_raw:
        raise PermanentError("gmail_personal: body missing")

    body_plain = _md_to_plain(body_raw)
    app_pass = _load_app_password()

    # ── build message ─────────────────────────────────────────────────────
    msg = EmailMessage()
    msg["From"] = from_header
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body_plain)

    # ── send ──────────────────────────────────────────────────────────────
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(FROM_ADDR, app_pass)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        _append_log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel": "gmail_personal",
            "status": "auth_failure",
            "to": to,
            "error": str(exc),
        })
        raise PermanentError(f"gmail_personal: SMTP auth failed — {exc}") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        _append_log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel": "gmail_personal",
            "status": "recipient_refused",
            "to": to,
            "error": str(exc),
        })
        raise PermanentError(f"gmail_personal: recipient refused: {to!r} — {exc}") from exc
    except smtplib.SMTPException as exc:
        _append_log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel": "gmail_personal",
            "status": "smtp_error",
            "to": to,
            "error": str(exc),
        })
        raise TransientError(f"gmail_personal: SMTP error — {exc}") from exc
    except OSError as exc:
        _append_log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel": "gmail_personal",
            "status": "network_error",
            "to": to,
            "error": str(exc),
        })
        raise TransientError(f"gmail_personal: network error — {exc}") from exc

    # ── success ───────────────────────────────────────────────────────────
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel": "gmail_personal",
        "status": "sent",
        "to": to,
        "subject": subject,
        "from": FROM_ADDR,
    }
    _append_log(entry)
    return {"status": "sent", "to": to, "subject": subject, "from": FROM_ADDR}
