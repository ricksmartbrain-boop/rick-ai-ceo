#!/usr/bin/env python3
"""Send queued email drafts from the mailbox outbox via Resend.

Pairs with email-sequence-dispatch.py: the dispatcher renders templates into
`~/rick-vault/mailbox/outbox/<sequence>/<stamp>-<email-slug>-step<N>.md`, and
this script delivers them. On success the draft file moves to
`~/rick-vault/mailbox/sent/<sequence>/...` so the outbox acts as a durable
queue. Suppression list at `~/rick-vault/mailbox/suppression.txt`.

Drafts may include an optional YAML-style frontmatter block:

    ---
    to: name@example.com
    subject: Welcome to Rick
    from: Rick <hello@meetrick.ai>
    ---

Without frontmatter, the recipient is resolved from the matching enrollment
in the sequence config and the subject falls back to sequence.default_subject
or `<sequence> step <N>`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
SEQUENCES_DIR = DATA_ROOT / "mailbox" / "sequences"
OUTBOX_DIR = DATA_ROOT / "mailbox" / "outbox"
SENT_DIR = DATA_ROOT / "mailbox" / "sent"
LOG_FILE = DATA_ROOT / "operations" / "email-sequence-send.jsonl"
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"

RESEND_ENDPOINT = "https://api.resend.com/emails"
DEFAULT_FROM = os.getenv("RICK_EMAIL_FROM") or os.getenv("MEETRICK_FROM_EMAIL") or "Rick <hello@meetrick.ai>"

FILENAME_RE = re.compile(r"^(?P<stamp>\d{8}-\d{6})-(?P<slug>.+)-step(?P<step>\d+)\.md$")


def now() -> datetime:
    return datetime.now()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_suppressions() -> set[str]:
    if not SUPPRESSION_FILE.exists():
        return set()
    try:
        lines = SUPPRESSION_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    result: set[str] = set()
    for line in lines:
        trimmed = line.strip()
        if trimmed and not trimmed.startswith("#"):
            result.add(trimmed.lower())
    return result


def parse_frontmatter(body: str) -> tuple[dict, str]:
    if not body.startswith("---\n") and not body.startswith("---\r\n"):
        return {}, body
    sep = "\n---\n"
    start_len = 4
    if body.startswith("---\r\n"):
        sep = "\r\n---\r\n"
        start_len = 5
    end = body.find(sep, start_len)
    if end == -1:
        return {}, body
    header = body[start_len:end]
    rest = body[end + len(sep):]
    meta: dict = {}
    for line in header.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip().strip('"')
    return meta, rest


_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def markdown_to_html(md: str) -> str:
    """Minimal markdown → HTML for transactional emails.

    Handles paragraphs, headings (# / ##), bold, italic, inline code, and links.
    For richer output, pre-render HTML into the template and pass via the
    `html_body` frontmatter field.
    """
    out: list[str] = []
    blocks = re.split(r"\n\s*\n", md.strip())
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        block = _LINK_RE.sub(r'<a href="\2">\1</a>', block)
        block = _BOLD_RE.sub(r"<strong>\1</strong>", block)
        block = _ITALIC_RE.sub(r"<em>\1</em>", block)
        block = _INLINE_CODE_RE.sub(r"<code>\1</code>", block)
        if block.startswith("# "):
            out.append(f"<h2>{block[2:].strip()}</h2>")
        elif block.startswith("## "):
            out.append(f"<h3>{block[3:].strip()}</h3>")
        else:
            out.append("<p>" + block.replace("\n", "<br/>") + "</p>")
    return "\n".join(out)


def find_enrollment(payload: dict, slug: str) -> dict | None:
    target = slug.lower()
    for enrollment in payload.get("enrollments", []):
        if not isinstance(enrollment, dict):
            continue
        email = str(enrollment.get("email", "")).lower()
        enroll_slug = email.replace("@", "-at-").replace(".", "-")
        if enroll_slug == target:
            return enrollment
    return None


def step_subject(payload: dict, step_num: int) -> str | None:
    for step in payload.get("steps", []):
        if isinstance(step, dict) and int(step.get("step", -1) or -1) == step_num:
            subj = step.get("subject")
            if subj:
                return str(subj)
    return None


def send_via_resend(*, to: str, subject: str, html: str, text: str, from_addr: str, api_key: str) -> tuple[bool, dict]:
    payload = {
        "from": from_addr,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        RESEND_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            try:
                return True, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return True, {"raw": raw}
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "ignore") if err.fp else ""
        return False, {"status": err.code, "body": body}
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
        return False, {"error": str(err)}


def append_log(events: list[dict]) -> None:
    if not events:
        return
    ensure_parent(LOG_FILE)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        for event in events:
            row = {"timestamp": now().isoformat(timespec="seconds"), **event}
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def html_to_text(html: str) -> str:
    text = re.sub(r"<br/?>", "\n", html)
    text = re.sub(r"</?p>", "\n\n", text)
    text = re.sub(r"</?(h2|h3)>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def resolve_recipient_and_subject(
    md_path: Path,
    meta: dict,
    sequence_config: dict | None,
    sequence_name: str,
) -> tuple[str | None, str, int | None]:
    match = FILENAME_RE.match(md_path.name)
    step_num = int(match.group("step")) if match else None
    slug = match.group("slug") if match else None

    to_email = meta.get("to")
    if not to_email and sequence_config and slug:
        enrollment = find_enrollment(sequence_config, slug)
        if enrollment:
            to_email = str(enrollment.get("email", "")) or None

    subject = meta.get("subject")
    if not subject and sequence_config and step_num is not None:
        subject = step_subject(sequence_config, step_num)
    if not subject and sequence_config:
        subject = sequence_config.get("default_subject")
    if not subject:
        subject = f"{sequence_name} — step {step_num}" if step_num is not None else sequence_name
    return to_email, str(subject), step_num


def deliver_draft(
    md_path: Path,
    sequence_name: str,
    sequence_config: dict | None,
    *,
    dry_run: bool,
    suppressions: set[str],
    api_key: str | None,
) -> dict:
    raw = md_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw)
    to_email, subject, step_num = resolve_recipient_and_subject(md_path, meta, sequence_config, sequence_name)

    if not to_email:
        return {"file": str(md_path), "sequence": sequence_name, "status": "missing-recipient"}

    normalized_to = to_email.lower()
    sent_target = SENT_DIR / sequence_name / md_path.name

    if normalized_to in suppressions:
        if not dry_run:
            ensure_parent(sent_target)
            md_path.rename(sent_target)
        return {
            "file": str(md_path),
            "sequence": sequence_name,
            "to": to_email,
            "status": "suppressed",
        }

    from_addr = meta.get("from") or DEFAULT_FROM
    html = meta.get("html_body") or markdown_to_html(body)
    text = meta.get("text_body") or html_to_text(html)

    # 2026-04-24: Fenix preflight on email drips. Observe mode default
    # (RICK_FENIX_LIVE!=1) — logs would-blocks to fenix-observed.jsonl
    # without gating. LIVE mode actually suppresses sends + alerts Vlad.
    # Heuristic checks: customer naming, pricing, refund/legal, founder
    # voice, MRR/ARR claims. Try/except shielded — gate cannot break the
    # send pipeline.
    fenix_blocked = False
    fenix_reason = ""
    try:
        from runtime.fenix_gate import preflight as _fenix_preflight
        # Build a fenix-friendly payload from the email content
        fenix_payload = {
            "subject": subject,
            "body": text or body,
            "to": to_email,
        }
        gate = _fenix_preflight(None, "email_drip", fenix_payload, job_id=md_path.name)
        if gate["action"] != "proceed":
            fenix_blocked = True
            fenix_reason = gate["reason"][:200]
    except Exception:
        # Defense: gate failure must never block a legit send
        pass

    if fenix_blocked:
        # Move draft to fenix-blocked dir for Vlad review (don't delete or send)
        fenix_blocked_dir = OUTBOX_DIR.parent / "fenix-blocked" / sequence_name
        try:
            ensure_parent(fenix_blocked_dir / md_path.name)
            md_path.rename(fenix_blocked_dir / md_path.name)
        except OSError:
            pass
        return {
            "file": str(md_path),
            "sequence": sequence_name,
            "to": to_email,
            "subject": subject,
            "step": step_num,
            "status": "fenix-blocked",
            "reason": fenix_reason,
        }

    if dry_run:
        return {
            "file": str(md_path),
            "sequence": sequence_name,
            "to": to_email,
            "subject": subject,
            "step": step_num,
            "status": "dry-run",
        }

    if not api_key:
        return {
            "file": str(md_path),
            "sequence": sequence_name,
            "to": to_email,
            "subject": subject,
            "status": "missing-resend-api-key",
        }

    ok, info = send_via_resend(
        to=to_email,
        subject=subject,
        html=html,
        text=text,
        from_addr=from_addr,
        api_key=api_key,
    )
    if ok:
        ensure_parent(sent_target)
        md_path.rename(sent_target)
        return {
            "file": str(sent_target),
            "sequence": sequence_name,
            "to": to_email,
            "subject": subject,
            "step": step_num,
            "status": "sent",
            "message_id": info.get("id"),
        }
    return {
        "file": str(md_path),
        "sequence": sequence_name,
        "to": to_email,
        "subject": subject,
        "step": step_num,
        "status": "send-failed",
        "error": info,
    }


def walk_outbox(*, dry_run: bool, suppressions: set[str], api_key: str | None) -> list[dict]:
    results: list[dict] = []
    if not OUTBOX_DIR.exists():
        return results
    for seq_dir in sorted(p for p in OUTBOX_DIR.iterdir() if p.is_dir()):
        sequence_name = seq_dir.name
        config_path = SEQUENCES_DIR / sequence_name / "sequence.json"
        sequence_config = load_json(config_path) if config_path.exists() else None
        for md_path in sorted(seq_dir.glob("*.md")):
            try:
                results.append(
                    deliver_draft(
                        md_path,
                        sequence_name,
                        sequence_config,
                        dry_run=dry_run,
                        suppressions=suppressions,
                        api_key=api_key,
                    )
                )
            except Exception as err:  # noqa: BLE001 — log and continue
                results.append(
                    {
                        "file": str(md_path),
                        "sequence": sequence_name,
                        "status": "exception",
                        "error": str(err),
                    }
                )
    return results


def command_send(dry_run: bool) -> int:
    suppressions = load_suppressions()
    api_key = os.getenv("RESEND_API_KEY")
    events = walk_outbox(dry_run=dry_run, suppressions=suppressions, api_key=api_key)
    if not dry_run:
        append_log(events)
    sent = sum(1 for e in events if e.get("status") == "sent")
    failed = sum(1 for e in events if e.get("status") in {"send-failed", "exception"})
    print(
        json.dumps(
            {
                "events": events,
                "count": len(events),
                "sent": sent,
                "failed": failed,
                "dry_run": dry_run,
            },
            indent=2,
        )
    )
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send queued email drafts via Resend")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List drafts that would be sent without calling Resend",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return command_send(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
