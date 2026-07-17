#!/usr/bin/env python3
"""Daily newsletter engine for Resend subscribers.

Run daily at 6am PT.
- Checks /subscribers endpoint (observability only, currently 405)
- Fetches Resend audience contacts
- Sends personal welcome emails to new external subscribers
- Welcome-only job. Weekly broadcasts belong exclusively to the Saturday
  "Rick Weekly Newsletter (Sat)" cron.
- Appends a human-readable run log to projects/email/newsletter-engine-log.md
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import time
import subprocess
import tempfile
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
LOCK_PATH = RICK_VAULT / "runtime" / "newsletter-engine.lock"
SKIPPED_ADDRESSES = RICK_VAULT / "logs" / "skipped-addresses.jsonl"
SUPPRESSION_FILE = RICK_VAULT / "mailbox" / "suppression.txt"
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


def request_json(
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | list[Any] | None = None,
    *,
    retries: int = 3,
    timeout: float = 30,
) -> tuple[int, Any]:
    if url.startswith(RESEND_BASE):
        auth = os.environ.get("RESEND_API_KEY", "").strip()
        body_path = None
        try:
            if payload is not None:
                data = json.dumps(payload)
            else:
                data = None

            for attempt in range(retries + 1):
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    body_path = tmp.name
                try:
                    cmd = [
                        "curl",
                        "-sS",
                        "-X",
                        method,
                        "-H",
                        f"Authorization: Bearer {auth}",
                        "-H",
                        "Accept: application/json",
                        "-A",
                        "rick-newsletter-engine/1.0",
                        url,
                        "-o",
                        body_path,
                        "-w",
                        "%{http_code}",
                    ]
                    if data is not None:
                        cmd.extend(["-H", "Content-Type: application/json", "--data", data])
                    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
                    http_status = (proc.stdout or "").strip() or "599"
                    try:
                        status = int(http_status)
                    except ValueError:
                        status = 599
                    body = Path(body_path).read_text(encoding="utf-8") if Path(body_path).exists() else ""
                    if status == 429 and attempt < retries:
                        retry_after = 1.0 + attempt
                        time.sleep(retry_after)
                        continue
                    try:
                        parsed = json.loads(body) if body else {}
                    except Exception:
                        parsed = {"raw": body}
                    return status, parsed
                finally:
                    if body_path and Path(body_path).exists():
                        try:
                            Path(body_path).unlink()
                        except Exception:
                            pass
        except subprocess.TimeoutExpired:
            return 599, {"error": "curl timeout"}

    headers = {}
    key = os.environ.get("RESEND_API_KEY", "").strip()
    if key and url.startswith(RESEND_BASE):
        headers["Authorization"] = f"Bearer {key}"
        headers["User-Agent"] = "rick-newsletter-engine/1.0"
        headers["Accept"] = "application/json"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    else:
        data = None
    req = Request(url, data=data, headers=headers, method=method)
    for attempt in range(retries + 1):
        try:
            with urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, json.loads(body) if body else {}
        except HTTPError as e:
            body = e.read().decode("utf-8")
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = {"raw": body}
            if e.code == 429 and attempt < retries:
                retry_after = e.headers.get("Retry-After")
                try:
                    sleep_for = float(retry_after) if retry_after else 1.0 + attempt
                except ValueError:
                    sleep_for = 1.0 + attempt
                time.sleep(sleep_for)
                continue
            return e.code, parsed
        except URLError as e:
            return 599, {"error": str(e)}

    return 599, {"error": "request retry loop exhausted"}


def get_all_contacts() -> list[Contact]:
    contacts: list[Contact] = []
    after = None
    while True:
        params = {"audience_id": AUDIENCE_ID, "limit": 100}
        if after:
            params["after"] = after
        status, data = request_json(f"{RESEND_CONTACTS}?{urlencode(params)}", timeout=120)
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


def load_suppression_set(path: Path = SUPPRESSION_FILE) -> set[str]:
    emails: set[str] = set()
    if not path.exists():
        return emails
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        token = raw.strip().split("#", 1)[0].strip().lower()
        if "@" not in token:
            continue
        emails.add(token.split()[0])
    return emails


def log_skipped_address(email: str, reason: str, stage: str) -> None:
    SKIPPED_ADDRESSES.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "stage": stage,
        "email": email,
        "reason": reason,
        "source": "newsletter-engine",
    }
    with SKIPPED_ADDRESSES.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def pre_send_check(email: str, stage: str, suppression_set: set[str]) -> tuple[bool, str]:
    normalized = email.strip().lower()
    if normalized in suppression_set:
        reason = "suppression_list"
        log_skipped_address(email, reason, stage)
        return False, reason
    try:
        repo_runtime = Path(__file__).resolve().parents[1] / "runtime"
        if str(repo_runtime) not in sys.path:
            sys.path.insert(0, str(repo_runtime))
        from email_validator import validate_for_outbound  # type: ignore
    except Exception as exc:
        reason = f"validator_unavailable:{type(exc).__name__}"
        log_skipped_address(email, reason, stage)
        return False, reason

    ok, reason = validate_for_outbound(email)
    if not ok:
        log_skipped_address(email, reason, stage)
    return ok, reason


def load_broadcast_sent_set(subject: str, sent_date: str) -> set[str]:
    sent: set[str] = set()
    if not NEWSLETTER_LOG.exists():
        return sent
    marker = f"## {sent_date} — {subject}"
    capture_next_recipient = False
    for raw in NEWSLETTER_LOG.read_text().splitlines():
        line = raw.strip()
        if line == marker:
            capture_next_recipient = True
            continue
        if capture_next_recipient:
            if line.startswith("- "):
                sent.add(line[2:].split("|", 1)[0].strip().lower())
            capture_next_recipient = False
    return sent


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


def send_email(
    to_email: str,
    subject: str,
    html: str | None = None,
    text: str | None = None,
) -> tuple[bool, str]:
    # Unified fail-closed per-recipient gate (2026-07-13): master kill +
    # RICK_EMAIL_SEND_LIVE + merged suppression/DNC. cold=False — newsletter
    # recipients are subscribers, cadence handled by the engine.
    try:
        wsroot = str(Path(__file__).resolve().parents[1])
        if wsroot not in sys.path:
            sys.path.insert(0, wsroot)
        from runtime.kill_switches import is_send_allowed

        allowed, gate_reason = is_send_allowed(to_email, cold=False)
    except Exception as exc:
        allowed, gate_reason = False, f"gate_unavailable:{type(exc).__name__}:{exc}"
    if not allowed:
        log_skipped_address(to_email, gate_reason, "send_gate")
        print(f"SEND_BLOCKED reason={gate_reason} to={to_email}", file=sys.stderr)
        return False, f"SEND_BLOCKED {gate_reason}"
    paused, reason = email_channel_paused()
    if paused:
        return False, f"email-channel-paused: {reason}"
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
        _ledger_send(to_email, subject, str(data["id"]))
        return True, str(data["id"])
    return False, json.dumps(data, ensure_ascii=False)


def _ledger_send(to_email: str, subject: str, resend_id: str) -> None:
    """Typed email-sends.jsonl row + channel counters so warmup caps and
    cross-sender 60m dedup see welcome sends (day14-gate excludes
    type=newsletter_welcome from outreach counts). The email is already out —
    an append failure must never flip the send to failed, so warn loud."""
    try:
        ledger = RICK_VAULT / "operations" / "email-sends.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to_email,
            "subject": subject,
            "status": "sent",
            "type": "newsletter_welcome",
            "resend_id": resend_id,
            "via": "newsletter-engine-run.py",
        }
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        wsroot = str(Path(__file__).resolve().parents[1])
        if wsroot not in sys.path:
            sys.path.insert(0, wsroot)
        from runtime.db import connect
        from runtime.kill_switches import record_send

        conn = connect()
        try:
            record_send(conn, "email")
        finally:
            conn.close()
    except Exception as exc:
        print(f"LEDGER APPEND FAILED for {to_email}: {type(exc).__name__}: {exc}", file=sys.stderr)


def email_channel_paused() -> tuple[bool, str]:
    forced_pause = os.environ.get("RICK_EMAIL_CHANNEL_PAUSED", "").strip().lower()
    if forced_pause in {"1", "true", "yes", "paused"}:
        return True, os.environ.get("RICK_EMAIL_CHANNEL_PAUSE_REASON", "forced pause").strip()
    # Real channel gate (2026-07-17): master kill + pause + quiet hours +
    # daily/per-minute caps + warmup ramp, fail CLOSED. The old hand-rolled
    # status=='paused' check missed all of those and failed OPEN on errors.
    try:
        wsroot = str(Path(__file__).resolve().parents[1])
        if wsroot not in sys.path:
            sys.path.insert(0, wsroot)
        from runtime.db import connect
        from runtime.kill_switches import ChannelPaused, assert_channel_active
    except Exception as exc:
        return True, f"channel_gate_unavailable:{type(exc).__name__}: {exc}"
    try:
        conn = connect()
        try:
            assert_channel_active(conn, "email")
        finally:
            conn.close()
        return False, ""
    except ChannelPaused as exc:
        return True, exc.reason
    except Exception as exc:
        return True, f"channel_gate_error:{type(exc).__name__}: {exc}"


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
    channel_paused, pause_reason = email_channel_paused()

    lines: list[str] = []
    if channel_paused:
        lines.append(f"Email channel paused: {pause_reason}")
    status, body = request_json(SUBSCRIBERS_URL)
    lines.append(f"/subscribers endpoint: {status}")
    lines.append(f"/subscribers body: {body.get('message') or body.get('raw') or body}")

    contacts = get_all_contacts()
    active_contacts = [c for c in contacts if not c.unsubscribed]
    external_contacts = [c for c in active_contacts if is_real_external(c.email)]
    welcome_sent = load_email_set(WELCOME_SENT)
    suppression_set = load_suppression_set()

    new_contacts = [c for c in external_contacts if c.created_at >= cutoff and c.email.lower() not in welcome_sent]
    if len(new_contacts) > 5:
        lines.append(f"Welcome cap active: {len(new_contacts)} eligible, sending first 5")
        new_contacts = new_contacts[:5]
    lines.append(f"Audience contacts: {len(contacts)}")
    lines.append(f"Active external contacts: {len(external_contacts)}")
    lines.append(f"New since yesterday UTC cutoff ({cutoff.isoformat()}): {len(new_contacts)}")

    welcomes_sent_now = []
    for contact in new_contacts:
        ok_to_send, validation_reason = pre_send_check(contact.email, "welcome", suppression_set)
        if not ok_to_send:
            lines.append(f"Welcome skipped by outbound validation: {contact.email} — {validation_reason}")
            continue
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

    lines.append("Warm follow-ups: disabled for this daily job")
    lines.append("Weekly broadcast: disabled for this daily job")

    # Comm-history digest: top 5 recipients by touch count last 7d
    lines.extend(_comm_digest_lines(days_back=7))

    lines.append("Summary: Daily nurture run completed")
    log_run(lines)

    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            message = "Newsletter engine already running; skipped overlapping invocation"
            log_run([message])
            print(message)
            raise SystemExit(0)
        raise SystemExit(main())
