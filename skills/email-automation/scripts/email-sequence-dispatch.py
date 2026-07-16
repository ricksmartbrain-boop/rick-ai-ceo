#!/usr/bin/env python3
"""Render due email-sequence steps into the outbox."""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
SEQUENCES_DIR = DATA_ROOT / "mailbox" / "sequences"
OUTBOX_DIR = DATA_ROOT / "mailbox" / "outbox"
LOG_FILE = DATA_ROOT / "operations" / "email-sequence-dispatch.jsonl"
RUNTIME_DB = Path(os.getenv("RICK_RUNTIME_DB_FILE", str(DATA_ROOT / "runtime" / "rick-runtime.db")))


def now() -> datetime:
    return datetime.now()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def add_one_month(anchor: datetime) -> datetime:
    """Same day-of-month one month out, clamped to month length (mirrors a
    monthly billing anchor; kept in sync with runtime/engine.py
    next_renewal_date — duplicated so this script stays dependency-free)."""
    year, month = (anchor.year + 1, 1) if anchor.month == 12 else (anchor.year, anchor.month + 1)
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return anchor.replace(year=year, month=month, day=day)


def renewal_date_display(enrollment: dict) -> str:
    """Value for the {{renewal_date}} token: explicit enrollment.renewal_date
    if set, else enrolled_at + 1 month (billing-anchor approximation), else a
    generic phrase — a literal '{{renewal_date}}' must never reach a customer."""
    renewal = parse_timestamp(str(enrollment.get("renewal_date", "")))
    if renewal is None:
        enrolled_at = parse_timestamp(str(enrollment.get("enrolled_at", "")))
        if enrolled_at is None:
            return "your next monthly billing date"
        renewal = add_one_month(enrolled_at)
    return f"{renewal:%B} {renewal.day}, {renewal.year}"


def render_template(raw: str, enrollment: dict) -> str:
    mapping = {
        "{{first_name}}": str(enrollment.get("first_name", "there")),
        "{{delivery_url}}": str(enrollment.get("delivery_url", "")),
        "{{product_name}}": str(enrollment.get("product_name", "")),
        "{{email}}": str(enrollment.get("email", "")),
        "{{renewal_date}}": renewal_date_display(enrollment),
    }
    rendered = raw
    for token, value in mapping.items():
        rendered = rendered.replace(token, value)
    return rendered


def renewal_block_reason(enrollment: dict) -> str | None:
    """Reason to withhold a renewal-class step (template 'renewal-*'), else None.

    Stripe cancels flip customers.status ('canceling'/'canceled', stripe-poll)
    but nothing closes the JSON sequence enrollment — without this check the
    day-25 "your subscription renews" notice goes to customers who already
    canceled (every voluntary cancel to date happened by day 23), a false
    renewal + access claim. Read-only lookup at draft time; fail-closed for
    this step only — a renewal claim that cannot be verified is not sent,
    while other steps and enrollees dispatch normally.
    """
    email = str(enrollment.get("email", "")).strip().lower()
    if not email:
        return "no-email"
    if not RUNTIME_DB.exists():
        return f"runtime-db-missing:{RUNTIME_DB}"
    try:
        connection = sqlite3.connect(f"file:{RUNTIME_DB}?mode=ro", uri=True, timeout=5)
        try:
            row = connection.execute("SELECT status FROM customers WHERE email = ?", (email,)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as err:
        return f"customers-lookup-failed:{err}"
    if row is not None and str(row[0]) in ("canceling", "canceled"):
        return f"customer-{row[0]}"
    return None


def due_steps(payload: dict, current_time: datetime) -> list[tuple[dict, dict]]:
    steps = payload.get("steps", [])
    enrollments = payload.get("enrollments", [])
    if not isinstance(steps, list) or not isinstance(enrollments, list):
        return []

    by_number = {
        int(step.get("step")): step
        for step in steps
        if isinstance(step, dict) and str(step.get("step", "")).isdigit()
    }
    due: list[tuple[dict, dict]] = []

    for enrollment in enrollments:
        if not isinstance(enrollment, dict) or enrollment.get("status") != "active":
            continue
        next_step_num = int(enrollment.get("current_step", 0) or 0) + 1
        step = by_number.get(next_step_num)
        if step is None:
            enrollment["status"] = "completed"
            continue
        enrolled_at = parse_timestamp(str(enrollment.get("enrolled_at", "")))
        if enrolled_at is None:
            continue
        due_at = enrolled_at + timedelta(days=int(step.get("delay_days", 0) or 0))
        if current_time >= due_at and next_step_num not in set(enrollment.get("sent_steps", [])):
            due.append((enrollment, step))
    return due


def dispatch_sequence(config_path: Path, *, current_time: datetime, dry_run: bool = False) -> list[dict]:
    payload = load_json(config_path)
    if not payload:
        return []

    sequence_name = str(payload.get("name", config_path.parent.name))
    dispatched: list[dict] = []
    sequence_outbox = OUTBOX_DIR / sequence_name
    ensure_parent(sequence_outbox / ".keep")

    for enrollment, step in due_steps(payload, current_time):
        if str(step.get("template", "")).startswith("renewal-"):
            block = renewal_block_reason(enrollment)
            if block is not None:
                if not dry_run and block.startswith("customer-"):
                    # Verified cancel: the rest of the sequence is moot; close
                    # the enrollment so a future re-purchase can re-enroll
                    # (find_active_sequence_enrollment only matches 'active').
                    enrollment["status"] = "cancelled"
                dispatched.append(
                    {
                        "sequence": sequence_name,
                        "email": enrollment.get("email", ""),
                        "step": int(step.get("step", 0) or 0),
                        "status": "renewal-withheld",
                        "reason": block,
                    }
                )
                continue
        template_path = config_path.parent / str(step.get("template", ""))
        if not template_path.exists():
            dispatched.append(
                {
                    "sequence": sequence_name,
                    "email": enrollment.get("email", ""),
                    "step": int(step.get("step", 0) or 0),
                    "status": "missing-template",
                    "template": str(template_path),
                }
            )
            continue

        rendered = render_template(template_path.read_text(encoding="utf-8"), enrollment)
        stamp = current_time.strftime("%Y%m%d-%H%M%S")
        email_slug = str(enrollment.get("email", "unknown")).replace("@", "-at-").replace(".", "-")
        outbox_path = sequence_outbox / f"{stamp}-{email_slug}-step{int(step.get('step', 0) or 0)}.md"

        if not dry_run:
            outbox_path.write_text(rendered.rstrip() + "\n", encoding="utf-8")
            enrollment["current_step"] = int(step.get("step", 0) or 0)
            enrollment["last_sent_at"] = current_time.isoformat(timespec="seconds")
            sent_steps = enrollment.get("sent_steps", [])
            if not isinstance(sent_steps, list):
                sent_steps = []
            sent_steps.append(int(step.get("step", 0) or 0))
            enrollment["sent_steps"] = sorted(set(sent_steps))
            if enrollment["current_step"] >= max(int(item.get("step", 0) or 0) for item in payload.get("steps", []) if isinstance(item, dict)):
                enrollment["status"] = "completed"

        dispatched.append(
            {
                "sequence": sequence_name,
                "email": enrollment.get("email", ""),
                "step": int(step.get("step", 0) or 0),
                "status": "drafted" if not dry_run else "due",
                "outbox_path": str(outbox_path),
            }
        )

    if dispatched and not dry_run:
        config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return dispatched


def append_log(events: list[dict]) -> None:
    if not events:
        return
    ensure_parent(LOG_FILE)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        for event in events:
            payload = {"timestamp": now().isoformat(timespec="seconds"), **event}
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def command_dispatch(dry_run: bool) -> int:
    if not SEQUENCES_DIR.exists():
        print(json.dumps({"dispatched": [], "count": 0}, indent=2))
        return 0

    current_time = now()
    events: list[dict] = []
    for config_path in sorted(SEQUENCES_DIR.glob("*/sequence.json")):
        events.extend(dispatch_sequence(config_path, current_time=current_time, dry_run=dry_run))

    if not dry_run:
        append_log(events)
    print(json.dumps({"dispatched": events, "count": len(events), "dry_run": dry_run}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dispatch due email sequence steps")
    parser.add_argument("--dry-run", action="store_true", help="Inspect due sequence steps without writing drafts")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return command_dispatch(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
