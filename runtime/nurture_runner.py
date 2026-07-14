"""nurture_runner.py — Process due nurture steps for /roast and /founder-tax leads.

Called from the heartbeat (not a standalone cron). Idempotent: safe to run every
heartbeat cycle — steps only fire when their due_at timestamp has passed and
sent_at is null.

Usage:
    python3 -m runtime.nurture_runner              # process due steps
    python3 -m runtime.nurture_runner --dry-run    # show what would send
    python3 -m runtime.nurture_runner --status     # lead count + pending steps
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Bootstrap path ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Env load ──────────────────────────────────────────────────────────────────
for _env_file in [
    Path.home() / "clawd" / "config" / "rick.env",
    Path.home() / ".openclaw" / "workspace" / "config" / "rick.env",
]:
    if _env_file.exists():
        for _line in _env_file.read_text().splitlines():
            _line = _line.strip()
            if _line.startswith("export "):
                _line = _line[7:]
            if "=" in _line and not _line.startswith("#"):
                k, _, v = _line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        break

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
STATE_FILE = DATA_ROOT / "operations" / "nurture-state.json"
ROAST_POLL_LOG = DATA_ROOT / "operations" / "roast-lead-poll.jsonl"
NURTURE_LOG = DATA_ROOT / "operations" / "nurture-runner.jsonl"

# Day offsets for each sequence step
STEP_DAYS = [0, 1, 2, 3, 5, 7]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _log(event: dict) -> None:
    NURTURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with NURTURE_LOG.open("a") as f:
        f.write(json.dumps({"ts": _now_iso(), **event}) + "\n")


# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load nurture-state.json. Returns empty state on missing/corrupt file."""
    if not STATE_FILE.exists():
        return {"leads": {}, "last_event_id": 0}
    try:
        data = json.loads(STATE_FILE.read_text())
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        data.setdefault("leads", {})
        data.setdefault("last_event_id", 0)
        return data
    except Exception as exc:
        print(f"[nurture_runner] state load error: {exc} — cold start", file=sys.stderr)
        return {"leads": {}, "last_event_id": 0}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Lead scanning ─────────────────────────────────────────────────────────────

def _build_steps(sequence: str, enroll_time: datetime) -> list[dict]:
    """Build the step schedule for a new lead."""
    steps = []
    for day in STEP_DAYS:
        due = enroll_time + timedelta(hours=day * 24)
        steps.append({
            "day": day,
            "due_at": due.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "sent_at": None,
        })
    return steps


def scan_new_leads(state: dict) -> int:
    """
    Read roast-lead-poll.jsonl for new entries not yet in state.
    Each new lead gets enrolled with a full step schedule starting now.
    Returns number of new leads added.
    """
    if not ROAST_POLL_LOG.exists():
        return 0

    last_id = state.get("last_event_id", 0)
    new_count = 0
    lines = ROAST_POLL_LOG.read_text().splitlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_id = row.get("id") or row.get("event_id") or 0
        # Skip events we've already processed (by integer id or string id)
        if isinstance(event_id, int) and event_id <= last_id:
            continue

        # Extract lead fields
        email = row.get("email") or row.get("lead_email")
        if not email or "@" not in email:
            continue

        # Skip if already enrolled
        if email in state["leads"]:
            continue

        # Determine sequence — roast-lead-poll events default to "roast"
        sequence = row.get("sequence", "roast")
        url = row.get("url") or row.get("lead_url") or row.get("site_url") or ""
        first_name = row.get("first_name") or row.get("name") or email.split("@")[0]
        enroll_ts = _parse_ts(row.get("ts") or row.get("created_at")) or _now()

        state["leads"][email] = {
            "email": email,
            "sequence": sequence,
            "ctx": {
                "lead_url": url,
                "first_name": first_name,
                "pdf_link": f"https://meetrick.ai/roast-pdf?url={url}" if url else "",
                "annual_bill": "",
                "leak_label": "",
                "leak_specific_paragraph": "",
            },
            "enrolled_at": enroll_ts.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "steps": _build_steps(sequence, enroll_ts),
        }
        new_count += 1
        _log({"event": "lead_enrolled", "email": email, "sequence": sequence, "url": url})

        # Update last_event_id
        if isinstance(event_id, int) and event_id > last_id:
            state["last_event_id"] = event_id

    return new_count


# ── Step processing ───────────────────────────────────────────────────────────

def process_due_steps(state: dict, dry_run: bool = False) -> int:
    """
    For each lead, find steps where due_at <= now and sent_at is None.
    Render + send via campaign-engine.send_email (which is gated).
    Returns number of steps attempted.
    """
    from runtime.nurture_sequences import sequence_for, render  # noqa: E402

    # Import send_email from campaign-engine
    import importlib.util
    _ce_path = ROOT / "scripts" / "campaign-engine.py"
    _spec = importlib.util.spec_from_file_location("campaign_engine", _ce_path)
    _ce = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_ce)  # type: ignore[union-attr]
    send_email = _ce.send_email

    now = _now()
    attempted = 0

    for email, lead in state["leads"].items():
        sequence_name = lead.get("sequence", "roast")
        ctx = lead.get("ctx", {})
        steps = lead.get("steps", [])

        for step in steps:
            if step.get("sent_at"):
                continue  # already sent

            due_at = _parse_ts(step.get("due_at"))
            if due_at is None or due_at > now:
                continue  # not due yet

            day = step["day"]
            # Render the email content
            try:
                seq = sequence_for(sequence_name)
                seq_step = next((s for s in seq if s["day"] == day), None)
                if seq_step is None:
                    _log({"event": "step_skip_no_template", "email": email, "day": day})
                    step["sent_at"] = _now_iso()  # mark sent to avoid retry loop
                    continue

                rendered = render(seq_step, ctx)
                subject = rendered["subject"]
                body = rendered.get("body_html") or rendered.get("body_md", "")
            except Exception as exc:
                _log({"event": "render_error", "email": email, "day": day, "error": str(exc)})
                continue

            attempted += 1

            if dry_run:
                print(f"[DRY-RUN] would send day={day} to={email} subject={subject[:60]}")
                _log({"event": "dry_run", "email": email, "day": day, "subject": subject})
                continue

            # Send through gated campaign-engine
            ok, detail = send_email(email, subject, body)
            if ok:
                step["sent_at"] = _now_iso()
                _log({"event": "step_sent", "email": email, "day": day, "subject": subject})
                print(f"[nurture_runner] sent day={day} to={email}")
            else:
                _log({"event": "step_failed", "email": email, "day": day, "detail": detail})
                print(f"[nurture_runner] send failed day={day} to={email}: {detail}", file=sys.stderr)

    return attempted


# ── Status ────────────────────────────────────────────────────────────────────

def print_status(state: dict) -> None:
    leads = state.get("leads", {})
    now = _now()
    total_leads = len(leads)
    pending = 0
    sent_total = 0

    for lead in leads.values():
        for step in lead.get("steps", []):
            if step.get("sent_at"):
                sent_total += 1
            else:
                due_at = _parse_ts(step.get("due_at"))
                if due_at and due_at <= now:
                    pending += 1

    print(f"Nurture runner status:")
    print(f"  Total leads enrolled: {total_leads}")
    print(f"  Steps sent (all time): {sent_total}")
    print(f"  Steps due now: {pending}")
    print(f"  Last event id processed: {state.get('last_event_id', 0)}")
    print(f"  State file: {STATE_FILE}")


# ── Entrypoint ────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> None:
    state = load_state()
    new_leads = scan_new_leads(state)
    if new_leads:
        print(f"[nurture_runner] enrolled {new_leads} new lead(s)")
    steps_run = process_due_steps(state, dry_run=dry_run)
    save_state(state)
    if steps_run == 0 and new_leads == 0:
        print("[nurture_runner] nothing to do")
    elif not dry_run:
        print(f"[nurture_runner] processed {steps_run} step(s)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Nurture sequence runner")
    ap.add_argument("--dry-run", action="store_true", help="show what would send, don't send")
    ap.add_argument("--status", action="store_true", help="show lead count + pending steps")
    args = ap.parse_args()

    if args.status:
        state = load_state()
        scan_new_leads(state)  # scan for display accuracy
        print_status(state)
        return

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
