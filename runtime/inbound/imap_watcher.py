#!/usr/bin/env python3
"""IMAP signature mining — pull inbound replies, dump to triage pipe + enrich.

Runs every 10min via ai.rick.imap-watcher.plist. Reads GMAIL_IMAP_USER +
GMAIL_APP_PASSWORD from rick.env. Connects to imap.gmail.com:993 via stdlib
imaplib. Walks UNSEEN in INBOX + optionally recent SEEN for 30d backfill.

For each message:
1. Parses headers + body (plain text preferred, HTML fallback).
2. Writes to ~/rick-vault/mailbox/triage/inbound-YYYY-MM-DD.jsonl
   (Phase G classifier + router pick it up).
3. Extracts signature via signature_parser → writes enrichment row into
   prospect_pipeline (UPDATE if email exists, INSERT if new).
4. Marks message \\Seen.

Stdlib only. Gated by RICK_IMAP_LIVE=1 (default dry-run).
"""
from __future__ import annotations

import argparse
import email
import email.policy
import hashlib
import imaplib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.inbound.signature_parser import extract_signature  # noqa: E402
from runtime.db import connect as db_connect  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
ENV_FILE = Path(os.getenv("RICK_ENV_FILE", str(Path.home() / "clawd" / "config" / "rick.env")))
TRIAGE_DIR = DATA_ROOT / "mailbox" / "triage"
STATE_FILE = DATA_ROOT / "mailbox" / "imap-watcher-state.json"
LOG_FILE = DATA_ROOT / "operations" / "imap-watcher.jsonl"

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
MAX_MESSAGES_PER_RUN = 200
EMAIL_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


def _load_env():
    if not ENV_FILE.exists():
        return
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(event: dict):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ran_at": _now_iso(), **event}) + "\n")
    except OSError:
        pass


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def _extract_addr(header_val: str) -> str:
    """Extract bare email from a 'Name <email@x.com>' header."""
    if not header_val:
        return ""
    m = EMAIL_ADDR_RE.search(header_val)
    return m.group(0).lower() if m else ""


def _extract_name(header_val: str) -> str:
    if not header_val:
        return ""
    # "Jamie Chen <jamie@acme.co>" → "Jamie Chen"
    if "<" in header_val:
        return header_val.split("<", 1)[0].strip().strip('"')
    return ""


def _message_body(msg: Message) -> tuple[str, str]:
    """Return (plain_text, html). Prefers text/plain."""
    plain = ""
    html = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and not plain:
                try:
                    plain = part.get_content() or ""
                except Exception:
                    try:
                        plain = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    except Exception:
                        pass
            elif ctype == "text/html" and not html:
                try:
                    html = part.get_content() or ""
                except Exception:
                    try:
                        html = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    except Exception:
                        pass
    else:
        try:
            content = msg.get_content() or ""
            if msg.get_content_type() == "text/html":
                html = content
            else:
                plain = content
        except Exception:
            try:
                plain = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            except Exception:
                pass
    return plain[:50000], html[:50000]


def _msg_hash(msg_id: str, from_: str, subject: str) -> str:
    return hashlib.sha1(f"{msg_id}|{from_}|{subject}".encode("utf-8", errors="ignore")).hexdigest()[:16]


def _write_triage(rows: list[dict]):
    """Append rows to today's triage JSONL — Phase G classifier picks up."""
    if not rows:
        return
    TRIAGE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = TRIAGE_DIR / f"inbound-{today}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def _enrich_prospect(conn: sqlite3.Connection, email: str, enrichment: dict) -> str:
    """UPSERT into prospect_pipeline with signature-derived enrichment."""
    if not email:
        return "skip-no-email"
    try:
        row = conn.execute(
            "SELECT id, notes_json FROM prospect_pipeline WHERE email = ? LIMIT 1",
            (email,),
        ).fetchone()
    except sqlite3.OperationalError:
        # prospect_pipeline may have different schema or not exist in this DB
        return "skip-no-table"
    if row:
        try:
            existing = json.loads(row["notes_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            existing = {}
        existing.setdefault("imap_enrichments", []).append({**enrichment, "at": _now_iso()})
        conn.execute(
            "UPDATE prospect_pipeline SET notes_json=?, updated_at=? WHERE id=?",
            (json.dumps(existing), _now_iso(), row["id"]),
        )
        return "updated"
    # Don't INSERT new prospects here — the IMAP sender may be noise
    # (newsletters, transactional). Only enrich if they already exist in pipeline.
    return "not-in-pipeline"


def process_messages(conn, mailbox, mail: imaplib.IMAP4_SSL, search_criteria: str, limit: int, dry_run: bool) -> dict:
    summary = {"fetched": 0, "triage_rows": 0, "enriched": 0, "signatures_found": 0}
    status, data = mail.search(None, search_criteria)
    if status != "OK":
        _log({"error": "search-failed", "criteria": search_criteria, "status": status})
        return summary
    ids = (data[0] or b"").split()[:limit]
    summary["fetched"] = len(ids)
    triage_rows = []
    for uid in ids:
        try:
            status, msg_data = mail.fetch(uid, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw, policy=email.policy.default)
        except Exception:
            continue

        from_ = msg.get("From", "") or ""
        from_email = _extract_addr(from_)
        from_name = _extract_name(from_)
        subject = msg.get("Subject", "") or ""
        message_id = msg.get("Message-ID", "") or ""
        received_at = msg.get("Date", "") or ""
        body_text, body_html = _message_body(msg)

        row = {
            "id": _msg_hash(message_id, from_email, subject),
            "message_id": message_id,
            "from": from_email,
            "from_name": from_name,
            "subject": subject[:200],
            "body": (body_text or "")[:8000],
            "has_html": bool(body_html),
            "received_at": received_at,
            "ingested_at": _now_iso(),
        }
        triage_rows.append(row)

        # Signature extraction + enrichment
        sig = extract_signature(body_text or body_html)
        if sig:
            summary["signatures_found"] += 1
            if not dry_run and from_email:
                action = _enrich_prospect(conn, from_email, sig)
                if action == "updated":
                    summary["enriched"] += 1

        if not dry_run:
            try:
                mail.store(uid, "+FLAGS", "\\Seen")
            except Exception:
                pass

    if not dry_run and triage_rows:
        _write_triage(triage_rows)
        conn.commit()
    summary["triage_rows"] = len(triage_rows)
    return summary


def run_watcher(dry_run: bool, backfill_days: int = 0) -> dict:
    imap_user = os.getenv("GMAIL_IMAP_USER") or os.getenv("IMAP_USER")
    imap_pass = os.getenv("GMAIL_APP_PASSWORD") or os.getenv("IMAP_PASSWORD")
    if not imap_user or not imap_pass:
        result = {"status": "skip-no-credentials", "hint": "Set GMAIL_IMAP_USER + GMAIL_APP_PASSWORD in ~/clawd/config/rick.env"}
        _log(result)
        return result

    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        mail.login(imap_user, imap_pass)
    except imaplib.IMAP4.error as exc:
        result = {"status": "auth-failed", "error": str(exc)[:200]}
        _log(result)
        return result

    try:
        mail.select("INBOX")
        conn = db_connect()
        try:
            # 1. Standard run: UNSEEN only (respects IMAP state)
            unseen = process_messages(conn, "INBOX", mail, "UNSEEN", MAX_MESSAGES_PER_RUN, dry_run)
            # 2. Optional backfill: last N days SEEN (first-run use)
            backfill = {"fetched": 0, "triage_rows": 0, "enriched": 0, "signatures_found": 0}
            if backfill_days > 0:
                since = (datetime.now() - timedelta(days=backfill_days)).strftime("%d-%b-%Y")
                backfill = process_messages(conn, "INBOX", mail, f'SINCE {since}', MAX_MESSAGES_PER_RUN, dry_run)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    finally:
        try:
            mail.close()
            mail.logout()
        except Exception:
            pass

    result = {
        "status": "ok",
        "dry_run": dry_run,
        "unseen": unseen,
        "backfill": backfill,
        "ran_at": _now_iso(),
    }
    _log(result)
    return result


def main() -> int:
    _load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    ap.add_argument("--backfill-days", type=int, default=0,
                    help="Pull SEEN messages from the last N days (0 = UNSEEN only)")
    args = ap.parse_args()

    # Even with --live, require master gate
    if not args.dry_run and os.getenv("RICK_IMAP_LIVE") != "1":
        args.dry_run = True

    result = run_watcher(args.dry_run, args.backfill_days)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
