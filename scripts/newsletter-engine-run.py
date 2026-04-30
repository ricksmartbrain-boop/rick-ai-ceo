#!/usr/bin/env python3
"""Daily newsletter engine for Resend subscribers.

Run daily at 6am PT.
- Checks /subscribers endpoint (observability only, currently 405)
- Fetches Resend audience contacts
- Sends personal welcome emails to new external subscribers
- Scans recent email activity for warm subscribers and sends a single follow-up
- On Sundays, sends the full weekly broadcast one recipient at a time
- Appends a human-readable run log to projects/email/newsletter-engine-log.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ENV_FILE = Path.home() / "clawd" / "config" / "rick.env"
RICK_VAULT = Path.home() / "rick-vault"
ENGINE_LOG = RICK_VAULT / "projects" / "email" / "newsletter-engine-log.md"
NEWSLETTER_LOG = RICK_VAULT / "projects" / "email" / "newsletter-log.md"
WELCOME_SENT = RICK_VAULT / "projects" / "email" / "welcome-sent.txt"
WARM_SENT = RICK_VAULT / "projects" / "email" / "warm-followup-sent.txt"
FROM_EMAIL = "Rick <rick@meetrick.ai>"
REPLY_TO = "rick@meetrick.ai"
AUDIENCE_ID = "fc739eb9-0e59-4aec-a6d0-5bf208b2b3dd"
SUBSCRIBERS_URL = "https://meetrick-subscribe-production.up.railway.app/subscribers"
RESEND_BASE = "https://api.resend.com"
RESEND_EMAILS = f"{RESEND_BASE}/emails"
RESEND_CONTACTS = f"{RESEND_BASE}/contacts"


@dataclass
class Contact:
    id: str
    email: str
    created_at: datetime
    unsubscribed: bool = False
    first_name: str | None = None
    last_name: str | None = None


@dataclass
class EmailEvent:
    id: str
    to_email: str
    subject: str
    created_at: datetime
    last_event: str


def load_env() -> None:
    if os.environ.get("RESEND_API_KEY"):
        return
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:]
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.strip().replace(" ", "T")
    normalized = normalized.replace("Z", "")
    normalized = re.sub(r"([+-]\d\d:?\d\d|[+-]\d\d)$", "", normalized)
    try:
        dt = datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        dt = datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


def request_json(url: str, method: str = "GET", payload: dict[str, Any] | list[Any] | None = None) -> tuple[int, Any]:
    headers = {}
    key = os.environ.get("RESEND_API_KEY", "").strip()
    if key and url.startswith(RESEND_BASE):
        headers["Authorization"] = f"Bearer {key}"
        headers["User-Agent"] = "resend-python/2.0.0"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    else:
        data = None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": body}
        return e.code, parsed
    except URLError as e:
        return 599, {"error": str(e)}


def get_all_contacts() -> list[Contact]:
    contacts: list[Contact] = []
    after = None
    while True:
        params = {"audience_id": AUDIENCE_ID, "limit": 100}
        if after:
            params["after"] = after
        status, data = request_json(f"{RESEND_CONTACTS}?{urlencode(params)}")
        if status != 200:
            raise RuntimeError(f"Resend contacts fetch failed: {status} {data}")
        page = data.get("data", []) or []
        for item in page:
            contacts.append(
                Contact(
                    id=item.get("id", ""),
                    email=item.get("email", ""),
                    created_at=parse_dt(item.get("created_at")),
                    unsubscribed=bool(item.get("unsubscribed")),
                    first_name=item.get("first_name"),
                    last_name=item.get("last_name"),
                )
            )
        if not data.get("has_more") or not page:
            break
        after = page[-1].get("id")
    return contacts


def get_all_emails(max_pages: int = 20) -> list[EmailEvent]:
    emails: list[EmailEvent] = []
    after = None
    pages = 0
    while pages < max_pages:
        params = {"limit": 100}
        if after:
            params["after"] = after
        status, data = request_json(f"{RESEND_EMAILS}?{urlencode(params)}")
        if status != 200:
            raise RuntimeError(f"Resend emails fetch failed: {status} {data}")
        page = data.get("data", []) or []
        for item in page:
            to_list = item.get("to") or []
            to_email = to_list[0] if to_list else ""
            emails.append(
                EmailEvent(
                    id=item.get("id", ""),
                    to_email=to_email,
                    subject=item.get("subject", ""),
                    created_at=parse_dt(item.get("created_at")),
                    last_event=(item.get("last_event") or "").lower(),
                )
            )
        pages += 1
        if not data.get("has_more") or not page:
            break
        after = page[-1].get("id")
    return emails


def load_email_set(path: Path) -> set[str]:
    emails: set[str] = set()
    if not path.exists():
        return emails
    for raw in path.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        emails.add(raw.split("|")[0].strip().lower())
    return emails


def append_email_record(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def is_real_external(email: str) -> bool:
    e = (email or "").strip().lower()
    if not e or "@" not in e:
        return False
    if e.endswith("@meetrick.ai"):
        return False
    if e.endswith("@belkins.io"):
        return False
    if e.startswith("rick+qa-") or e.startswith("rick+test-"):
        return False
    if e.startswith("test-") or e.startswith("qa-"):
        return False
    if "example.com" in e:
        return False
    return True


def first_name_from_email(email: str) -> str:
    local = email.split("@", 1)[0]
    for prefix in ("info", "hello", "contact", "admin", "support", "team", "office", "help"):
        if local.lower() == prefix:
            return "there"
    name = re.split(r"[._-]", local)[0]
    if len(name) < 2 or name.isdigit():
        return "there"
    return name.capitalize()


def send_email(to_email: str, subject: str, html: str | None = None, text: str | None = None) -> tuple[bool, str]:
    payload: dict[str, Any] = {
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
    }
    if html is not None:
        payload["html"] = html
    if text is not None:
        payload["text"] = text
    status, data = request_json(RESEND_EMAILS, method="POST", payload=payload)
    if status in (200, 201) and data.get("id"):
        return True, str(data["id"])
    return False, json.dumps(data, ensure_ascii=False)


def welcome_template(email: str) -> tuple[str, str]:
    name = first_name_from_email(email)
    subject = "Welcome aboard"
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;line-height:1.6;color:#111">
  <p>Hey {name},</p>
  <p>Thanks for subscribing. You’re on the list now.</p>
  <p>I keep this one simple, practical, and weirdly honest, real numbers, real tests, no newsletter perfume.</p>
  <p>If you ever want something more specific, just reply.</p>
  <p>— Rick</p>
</div>
""".strip()
    return subject, html


def warm_template(email: str, event: EmailEvent) -> tuple[str, str, str]:
    name = first_name_from_email(email)
    signal = "clicked" if event.last_event == "clicked" else "opened"
    subject = f"Saw you {signal} that note"
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;line-height:1.6;color:#111">
  <p>Hey {name},</p>
  <p>Saw you {signal} the last email.</p>
  <p>Curious what caught your eye. If you want, reply with one sentence and I’ll make the next note more useful.</p>
  <p>— Rick</p>
</div>
""".strip()
    return subject, html, signal


def broadcast_template() -> tuple[str, str]:
    week = datetime.now().strftime("%B %d")
    subject = f"The Rick Report — Week of {week}"
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:640px;line-height:1.7;color:#111">
  <p>Hey,</p>
  <p>This week’s Rick Report is ready.</p>
  <p>Real numbers, real shipping, and whatever tiny chaos paid the bills.</p>
  <p>— Rick</p>
</div>
""".strip()
    return subject, html


# ---------------------------------------------------------------------------
# Comm-history suppression helper
# ---------------------------------------------------------------------------

def _comm_suppressed(email: str) -> tuple[bool, str]:
    """Check comm_history for negative signals (bounce, unsubscribe, not_interested).
    Non-fatal: returns (False, "") if comm_history is unavailable.
    """
    try:
        import sys as _sys
        import os as _os
        _repo = str(Path(__file__).resolve().parents[1])
        if _repo not in _sys.path:
            _sys.path.insert(0, _repo)
        from runtime.comm_history import is_suppressed as _ch_suppressed
        return _ch_suppressed(email, days_back=90)
    except Exception:
        return False, ""


def _comm_digest_lines(days_back: int = 7) -> list[str]:
    """Return digest lines for top-5 recipients by touch count."""
    try:
        import sys as _sys
        _repo = str(Path(__file__).resolve().parents[1])
        if _repo not in _sys.path:
            _sys.path.insert(0, _repo)
        from runtime.comm_history import get_digest_top5, format_digest_line
        top5 = get_digest_top5(days_back=days_back)
        return [format_digest_line(top5, days_back)]
    except Exception:
        return []


def log_run(lines: list[str]) -> None:
    entry = [
        f"## {datetime.now().strftime('%Y-%m-%d %H:%M %Z')} — Daily Nurture Run",
        *[f"- {line}" for line in lines],
        "",
    ]
    append_email_record(ENGINE_LOG, "\n".join(entry))


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()
    if not os.environ.get("RESEND_API_KEY"):
        print("RESEND_API_KEY missing", file=sys.stderr)
        return 1

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=1)
    today_weekday = now_utc.isoweekday()

    lines: list[str] = []
    lines.append(f"/subscribers endpoint: {request_json(SUBSCRIBERS_URL)[0]}")
    status, body = request_json(SUBSCRIBERS_URL)
    lines.append(f"/subscribers body: {body.get('message') or body.get('raw') or body}")

    contacts = get_all_contacts()
    active_contacts = [c for c in contacts if not c.unsubscribed]
    external_contacts = [c for c in active_contacts if is_real_external(c.email)]
    welcome_sent = load_email_set(WELCOME_SENT)
    warm_sent = load_email_set(WARM_SENT)

    new_contacts = [c for c in external_contacts if c.created_at >= cutoff and c.email.lower() not in welcome_sent]
    lines.append(f"Audience contacts: {len(contacts)}")
    lines.append(f"Active external contacts: {len(external_contacts)}")
    lines.append(f"New since yesterday UTC cutoff ({cutoff.isoformat()}): {len(new_contacts)}")

    welcomes_sent_now = []
    for contact in new_contacts:
        _suppressed, _supp_reason = _comm_suppressed(contact.email)
        if _suppressed:
            lines.append(f"Welcome suppressed (comm_history): {contact.email} — {_supp_reason}")
            continue
        subject, html = welcome_template(contact.email)
        if args.dry_run:
            ok, detail = True, "dry-run"
        else:
            ok, detail = send_email(contact.email, subject, html=html)
        if ok:
            welcomes_sent_now.append((contact.email, detail))
            lines.append(f"Welcome sent: {contact.email} -> {detail}")
            if not args.dry_run:
                append_email_record(WELCOME_SENT, f"{contact.email}|{now_utc.date().isoformat()}|{detail}")
                time.sleep(0.35)
        else:
            lines.append(f"Welcome send failed: {contact.email} -> {detail}")

    lines.append(f"Welcome emails sent: {len(welcomes_sent_now)}")

    emails = get_all_emails(max_pages=20)
    latest_warm: dict[str, EmailEvent] = {}
    external_set = {c.email.lower() for c in external_contacts}
    for event in emails:
        email = event.to_email.lower().strip()
        if email not in external_set:
            continue
        if event.last_event not in {"opened", "clicked"}:
            continue
        if email in latest_warm:
            continue
        latest_warm[email] = event

    warm_candidates = [email for email, event in latest_warm.items() if email not in warm_sent]
    lines.append(f"Warm contacts found: {len(latest_warm)}")

    warm_sent_now = []
    for email in warm_candidates:
        _suppressed, _supp_reason = _comm_suppressed(email)
        if _suppressed:
            lines.append(f"Warm suppressed (comm_history): {email} — {_supp_reason}")
            continue
        event = latest_warm[email]
        subject, html, signal = warm_template(email, event)
        if args.dry_run:
            ok, detail = True, "dry-run"
        else:
            ok, detail = send_email(email, subject, html=html)
        if ok:
            warm_sent_now.append((email, signal, detail))
            lines.append(f"Warm follow-up sent: {email} ({signal}) -> {detail}")
            if not args.dry_run:
                append_email_record(WARM_SENT, f"{email}|{now_utc.date().isoformat()}|warm-followup-{signal}|{detail}")
                time.sleep(0.35)
        else:
            lines.append(f"Warm follow-up failed: {email} -> {detail}")

    lines.append(f"Warm follow-ups sent now: {len(warm_sent_now)}")

    if today_weekday == 7:
        subject, html = broadcast_template()
        broadcast_targets = [c for c in external_contacts if c.email.lower() not in warm_sent]
        lines.append(f"Sunday detected, broadcast target count: {len(broadcast_targets)}")
        if args.dry_run:
            lines.append(f"Broadcast draft ready: {subject}")
        else:
            sent = 0
            suppressed_count = 0
            for contact in broadcast_targets:
                _suppressed, _supp_reason = _comm_suppressed(contact.email)
                if _suppressed:
                    suppressed_count += 1
                    continue
                ok, detail = send_email(contact.email, subject, html=html)
                if ok:
                    sent += 1
                    append_email_record(NEWSLETTER_LOG, f"## {now_utc.date().isoformat()} — {subject}\n- {contact.email} | {detail}")
            lines.append(f"Broadcast sent to active external audience: {sent} (suppressed by comm_history: {suppressed_count})")
    else:
        lines.append("Sunday broadcast: skipped (not Sunday)")

    # Comm-history digest: top 5 recipients by touch count last 7d
    lines.extend(_comm_digest_lines(days_back=7))

    lines.append("Summary: Daily nurture run completed")
    log_run(lines)

    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
