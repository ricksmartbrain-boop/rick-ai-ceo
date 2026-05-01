#!/usr/bin/env python3
"""validate-existing-leads.py — One-shot MX validation of active qualified_lead workflows.

Walks all active/queued qualified_lead workflows in the runtime DB, runs
validate_for_outbound() on each email, and cancels any with a bad address.

Cancelled workflows get:
  - status='cancelled'
  - stage='bad-address'

The bad email is also appended to ~/rick-vault/mailbox/suppression.txt.

Usage:
  python3 scripts/validate-existing-leads.py [--dry-run] [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load env
ENV_CANDIDATES = [
    Path(os.getenv("RICK_ENV_FILE", str(Path.home() / "clawd" / "config" / "rick.env"))),
    ROOT / "config" / "rick.env",
]
for _ec in ENV_CANDIDATES:
    if not _ec.exists():
        continue
    for _line in _ec.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line.startswith("export "):
            _line = _line[7:]
        if "=" in _line and not _line.startswith("#"):
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from runtime.email_validator import validate_for_outbound  # noqa: E402

VAULT = Path.home() / "rick-vault"
SUPPRESSION_FILE = VAULT / "mailbox" / "suppression.txt"
NOW_UTC = datetime.now(timezone.utc).isoformat(timespec="seconds")

# Default DB path (same resolution as runner.py / engine.py)
DEFAULT_DB = (
    Path(os.getenv("RICK_RUNTIME_DB_FILE", ""))
    or VAULT / "runtime" / "rick-runtime.db"
)


def _json_loads(s: str | None) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}


def extract_email(context_json: str | None) -> str:
    """Pull email from flat or trigger_payload-wrapped context_json."""
    ctx = _json_loads(context_json)
    trigger = ctx.get("trigger_payload", ctx)
    email = (
        trigger.get("email") or
        trigger.get("lead_email") or
        ctx.get("email") or
        ctx.get("lead_email") or
        ""
    )
    return (email or "").strip().lower()


def append_suppression(email: str, reason: str) -> None:
    """Append email to suppression.txt."""
    SUPPRESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SUPPRESSION_FILE, "a", encoding="utf-8") as f:
        f.write(f"{email}  # {reason} — auto-suppressed {NOW_UTC[:10]} by validate-existing-leads.py\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate emails of active qualified_lead workflows")
    ap.add_argument("--dry-run", action="store_true", help="Print actions without writing to DB")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="Path to runtime SQLite DB")
    ap.add_argument(
        "--statuses", default="active,queued",
        help="Comma-separated workflow statuses to scan (default: active,queued)"
    )
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"❌ DB not found: {db_path}")
        sys.exit(1)

    statuses = [s.strip() for s in args.statuses.split(",")]
    placeholders = ",".join("?" * len(statuses))

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row

    rows = db.execute(
        f"SELECT id, kind, status, stage, context_json "
        f"FROM workflows "
        f"WHERE kind='qualified_lead' AND status IN ({placeholders})",
        statuses,
    ).fetchall()

    print(f"\nvalidate-existing-leads")
    print(f"{'='*56}")
    print(f"DB:       {db_path}")
    print(f"Statuses: {statuses}")
    print(f"Workflows found: {len(rows)}")
    if args.dry_run:
        print("MODE: DRY RUN — no writes\n")
    else:
        print()

    total = len(rows)
    cancelled = 0
    already_good = 0
    no_email = 0

    for row in rows:
        wf_id = row["id"]
        email = extract_email(row["context_json"])

        if not email or "@" not in email:
            print(f"  ⚠  {wf_id}  no email found in context_json — skipping")
            no_email += 1
            continue

        ok, reason = validate_for_outbound(email)

        if ok:
            print(f"  ✅ {wf_id}  {email:<42}  ok")
            already_good += 1
        else:
            print(f"  ❌ {wf_id}  {email:<42}  CANCEL reason={reason}")
            if not args.dry_run:
                db.execute(
                    "UPDATE workflows SET status='cancelled', stage='bad-address', "
                    "updated_at=? WHERE id=?",
                    (NOW_UTC, wf_id),
                )
                append_suppression(email, reason)
            cancelled += 1

    if not args.dry_run:
        db.commit()

    db.close()

    print(f"\n{'='*56}")
    print(f"Total scanned:  {total}")
    print(f"Good (kept):    {already_good}")
    print(f"Cancelled:      {cancelled}")
    print(f"No-email skip:  {no_email}")
    if not args.dry_run and cancelled > 0:
        print(f"Suppression:    {SUPPRESSION_FILE}")
    if args.dry_run:
        print("(dry-run — no changes written)")


if __name__ == "__main__":
    main()
