"""winback_scheduler.py — Day-30 win-back drafts for lapsed subscribers.

Called once per UTC day from runtime.nurture_runner (daemon loop + heartbeat
— NOT a new job). Scans runtime-DB customers whose subscription lapsed 28-32
days ago and drafts ONE win-back outbox item per customer, ever
(status=held_pending_owner — the founder-batch-1 first-instance trust
pattern). The 900s outbox drain only sends status='pending', so nothing goes
out until the owner flips the status. The body contains NO discount; if a
make-good looks worth offering, the item PROPOSES it to the owner in
`make_good_proposal` metadata instead.

DB access is SELECT-only. Dedupe is forever: operations/winback-state.json
plus a deterministic filename (winback-<email-slug>.json) checked in both
mailbox/outbox/ and mailbox/sent/.

Usage:
    python3 -m runtime.winback_scheduler            # daily-gated scan
    python3 -m runtime.winback_scheduler --dry-run  # show matches, write nothing
    python3 -m runtime.winback_scheduler --force    # bypass the daily gate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

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
OUTBOX_DIR = DATA_ROOT / "mailbox" / "outbox"
SENT_DIR = DATA_ROOT / "mailbox" / "sent"
STATE_FILE = DATA_ROOT / "operations" / "winback-state.json"
WINBACK_LOG = DATA_ROOT / "operations" / "winback-scheduler.jsonl"
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"
CANCEL_REASONS = DATA_ROOT / "churn" / "cancel-reasons.jsonl"

# Lapse window: 28-32 days keeps a daily scan from ever missing day 30.
WINDOW_MIN_DAYS = 28
WINDOW_MAX_DAYS = 32


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _log(event: dict) -> None:
    WINBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with WINBACK_LOG.open("a") as f:
        f.write(json.dumps({"ts": _now_iso(), **event}) + "\n")


# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load winback-state.json. Returns empty state on missing/corrupt file."""
    if not STATE_FILE.exists():
        return {"last_scan_date": "", "queued": {}}
    try:
        data = json.loads(STATE_FILE.read_text())
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        data.setdefault("last_scan_date", "")
        data.setdefault("queued", {})
        return data
    except Exception as exc:
        print(f"[winback] state load error: {exc} — cold start (filename dedupe still holds)",
              file=sys.stderr)
        return {"last_scan_date": "", "queued": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Helpers ───────────────────────────────────────────────────────────────────

def email_slug(email: str) -> str:
    """Same slug convention as email-sequence-dispatch.py."""
    return email.strip().lower().replace("@", "-at-").replace(".", "-")


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def load_suppressions() -> set[str]:
    if not SUPPRESSION_FILE.exists():
        return set()
    suppressed: set[str] = set()
    for raw in SUPPRESSION_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        email = raw.split("#", 1)[0].strip().lower()
        if email:
            suppressed.add(email)
    return suppressed


def cancel_reason_for(email: str, payload: dict) -> str:
    """Durable cancel reason if one exists: churn/cancel-reasons.jsonl first
    (latest matching row wins), then the subscription event payload.

    Both producers key the address as "customer" (stripe-poll harvest +
    reply-triage capture, cd53425). Quote material in preference order:
    verbatim_text (the customer's own reply) > comment > feedback (Stripe
    survey). Machine placeholders/enums ('none', reason codes like
    cancellation_requested) are never quoted back at a customer."""
    reason = ""
    if CANCEL_REASONS.exists():
        for raw in CANCEL_REASONS.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            addr = str(row.get("customer") or row.get("email") or "").strip().lower()
            if addr != email.lower():
                continue
            for key in ("verbatim_text", "comment", "feedback"):
                val = str(row.get(key) or "").strip()
                if val and val.lower() != "none":
                    reason = val
                    break
    if not reason:
        reason = str(payload.get("cancel_reason") or payload.get("feedback") or "").strip()
    return " ".join(reason.split())[:200]


# ── Candidate scan (SELECT-only) ──────────────────────────────────────────────

def lapsed_candidates(conn) -> tuple[list[dict], dict]:
    """Customers in the 28-32d post-lapse window. Returns (candidates, counters).

    Lapse date = end_date from the latest subscription_status_changed event,
    falling back to metadata current_period_end. Customers still 'active'
    (e.g. resubscribed before day 28) are never candidates.
    """
    counters = {"lapsed_outside_window": 0, "not_lapsed_yet": 0, "no_lapse_date": 0}
    candidates: list[dict] = []
    today = _today()

    rows = conn.execute(
        "SELECT id, email, name, status, metadata_json, created_at FROM customers "
        "WHERE status IN ('canceling', 'canceled')"
    ).fetchall()

    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}

        event = conn.execute(
            "SELECT payload_json, created_at FROM customer_events "
            "WHERE customer_id = ? AND event_type = 'subscription_status_changed' "
            "ORDER BY created_at DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        payload: dict = {}
        if event is not None:
            try:
                payload = json.loads(event["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}

        end_date = _parse_date(payload.get("end_date")) or _parse_date(
            metadata.get("current_period_end")
        )
        if end_date is None:
            counters["no_lapse_date"] += 1
            print(f"[winback] SKIP {row['email']}: status={row['status']} but no lapse "
                  f"date in events or metadata — fix the data", file=sys.stderr)
            _log({"event": "skip_no_lapse_date", "email": row["email"],
                  "customer_id": row["id"], "status": row["status"]})
            continue

        days_since = (today - end_date).days
        if days_since < 0:
            counters["not_lapsed_yet"] += 1
            continue
        if not (WINDOW_MIN_DAYS <= days_since <= WINDOW_MAX_DAYS):
            counters["lapsed_outside_window"] += 1
            continue

        candidates.append({
            "customer_id": row["id"],
            "email": row["email"].strip().lower(),
            "name": row["name"] or "",
            "status": row["status"],
            "metadata": metadata,
            "payload": payload,
            "signup_date": str(row["created_at"] or "")[:10],
            "end_date": end_date.isoformat(),
            "days_since_lapse": days_since,
        })
    return candidates, counters


# ── Draft ─────────────────────────────────────────────────────────────────────

def build_item(cand: dict) -> dict:
    """Held outbox item. Body: short, honest, references real history, NO
    discounts. Make-good is proposed to the owner in metadata only."""
    metadata = cand["metadata"]
    payload = cand["payload"]
    email = cand["email"]

    product = str(
        metadata.get("product_name") or metadata.get("source_workflow_title") or ""
    ).strip() or "your subscription"
    amount = metadata.get("amount_usd") or metadata.get("first_purchase_amount_usd") or ""
    name = cand["name"].strip()
    first_name = name.split()[0] if name else email.split("@")[0]
    # subscription_status_changed payloads carry no canceled_at — the customers
    # metadata row does (e.g. russian@'s Nat-pattern sync). Payload kept as
    # fallback in case a future producer adds it.
    cancel_date = str(metadata.get("canceled_at") or payload.get("canceled_at") or "")[:10]
    reason = cancel_reason_for(email, payload)

    if "lingualive" in product.lower():
        comeback_line = "If you want back in, your login still works: https://www.lingualive.ai"
    elif "rick" in product.lower():
        comeback_line = "If you want back in: https://meetrick.ai"
    else:
        comeback_line = "If you want back in, just reply to this email and I will sort it out."

    if reason:
        reason_para = (f'When you canceled you told us: "{reason}". That was useful - '
                       "thank you. If we have not fixed it yet, I would rather hear that too.")
    elif cancel_date:
        reason_para = (f"You canceled on {cancel_date} and never said why - totally fine, "
                       "but if there was one specific reason, I would genuinely like to know.")
    else:
        reason_para = ("You never told us why you left - totally fine, but if there was "
                       "one specific reason, I would genuinely like to know.")

    subject = f"Your {product} access ended a month ago - one honest question"
    signup_bit = f"You signed up for {product}"
    if cand["signup_date"]:
        signup_bit += f" on {cand['signup_date']}"
    body = (
        f"**Subject:** {subject}\n\n"
        f"Hi {first_name},\n\n"
        f"Rick here. {signup_bit}, and your access ended on {cand['end_date']} - "
        "about a month ago now.\n\n"
        f"{reason_para}\n\n"
        "This is the only win-back note I will send - no drip campaign. One honest\n"
        "question: is there anything that would make it worth coming back? If\n"
        "something was broken or missing, tell me and I will either fix it or say\n"
        "straight that we cannot.\n\n"
        f"{comeback_line}\n\n"
        "Either way, thanks for giving it a real shot.\n\n"
        "-- Rick"
    )

    if payload.get("cancel_at_period_end") is False:
        make_good = ("Looks like involuntary churn (Stripe ended it, no cancel_at_period_end "
                     "- payment-failure class). Suggest offering a clean restart with an "
                     "updated-card link, not a discount. Not in the draft body; add it only "
                     "if you approve.")
    else:
        make_good = ("If a make-good is worth it: prior saves offered a free month, so the "
                     "same offer would be consistent. It is NOT in the draft body - Rick "
                     "does not send discounts without owner approval. Edit the body before "
                     "release if you approve one.")

    return {
        "to": email,
        "status": "held_pending_owner",
        "type": "winback",
        "product": product,
        "cold": False,
        "subject": subject,
        "body_markdown": body,
        "created_at": _now_iso(),
        "source": "winback-scheduler",
        "customer_id": cand["customer_id"],
        "lapsed_at": cand["end_date"],
        "days_since_lapse": cand["days_since_lapse"],
        "cancel_reason": reason,
        "make_good_proposal": make_good,
        "hold_reason": ("first-instance win-back (marketing-class) held for owner approval "
                        "- founder-batch-1 trust pattern. To release: set status to "
                        "'pending' (+ owner_decision note) and the 900s outbox drain sends "
                        "it through the full send gate."),
        "note": (f"history: signed up {cand['signup_date'] or 'unknown'}, paid "
                 f"${amount or '?'}, canceled {cancel_date or 'unknown'}, lapsed "
                 f"{cand['end_date']}. One win-back per customer EVER - dedupe in "
                 "operations/winback-state.json + this filename."),
    }


# ── Run ───────────────────────────────────────────────────────────────────────

def run_daily(dry_run: bool = False, force: bool = False) -> dict:
    """Daily-gated scan. Safe to call every heartbeat/daemon tick."""
    today_str = _today().isoformat()
    state = load_state()
    if not force and not dry_run and state.get("last_scan_date") == today_str:
        return {"status": "already_ran", "date": today_str}

    from runtime.db import connect  # noqa: E402  (SELECT-only below)

    conn = connect()
    try:
        candidates, counters = lapsed_candidates(conn)
    finally:
        conn.close()

    suppressions = load_suppressions()
    queued = 0
    skipped_dedupe = 0
    skipped_suppressed = 0

    for cand in candidates:
        email = cand["email"]
        slug = email_slug(email)
        if not slug.strip("-"):
            # Empty-slug guard ('-post-purchase' bug class): never write a
            # nameless item; surface and move on.
            print(f"[winback] SKIP empty slug for customer {cand['customer_id']}",
                  file=sys.stderr)
            _log({"event": "skip_empty_slug", "customer_id": cand["customer_id"]})
            continue
        filename = f"winback-{slug}.json"

        if email in state["queued"]:
            skipped_dedupe += 1
            continue
        if (OUTBOX_DIR / filename).exists() or (SENT_DIR / filename).exists():
            skipped_dedupe += 1
            if not dry_run:  # backfill state so the cheap check catches it next time
                state["queued"][email] = {"customer_id": cand["customer_id"],
                                          "file": filename, "queued_at": _now_iso(),
                                          "status": "preexisting_file"}
            continue
        if email in suppressions:
            skipped_suppressed += 1
            _log({"event": "skip_suppressed", "email": email,
                  "customer_id": cand["customer_id"]})
            if not dry_run:  # suppressed = never email = win-back closed forever
                state["queued"][email] = {"customer_id": cand["customer_id"],
                                          "file": "", "queued_at": _now_iso(),
                                          "status": "skipped_suppressed"}
            continue

        item = build_item(cand)
        if dry_run:
            print(f"[DRY-RUN] would hold win-back for {email} "
                  f"(day {cand['days_since_lapse']} post-lapse) -> {filename}")
            queued += 1
            continue

        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTBOX_DIR / filename
        path.write_text(json.dumps(item, indent=2), encoding="utf-8")
        path.chmod(0o600)
        state["queued"][email] = {"customer_id": cand["customer_id"], "file": filename,
                                  "queued_at": _now_iso(), "lapsed_at": cand["end_date"],
                                  "status": "held_pending_owner"}
        queued += 1
        print(f"[winback] HELD win-back for {email} (day {cand['days_since_lapse']} "
              f"post-lapse) -> {filename} — awaiting owner release")
        _log({"event": "winback_held", "email": email, "file": filename,
              "customer_id": cand["customer_id"], "lapsed_at": cand["end_date"],
              "days_since_lapse": cand["days_since_lapse"]})

    summary = {"status": "scanned", "date": today_str, "dry_run": dry_run,
               "in_window": len(candidates), "queued": queued,
               "skipped_dedupe": skipped_dedupe,
               "skipped_suppressed": skipped_suppressed, **counters}
    if not dry_run:
        state["last_scan_date"] = today_str
        save_state(state)
        _log({"event": "scan_complete", **summary})
    print(f"[winback] scan {today_str}: {queued} held, {skipped_dedupe} deduped, "
          f"{skipped_suppressed} suppressed, {counters['not_lapsed_yet']} not lapsed yet, "
          f"{counters['lapsed_outside_window']} outside 28-32d window"
          + (" (dry-run)" if dry_run else ""))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Day-30 win-back scheduler (held drafts)")
    ap.add_argument("--dry-run", action="store_true", help="show matches, write nothing")
    ap.add_argument("--force", action="store_true", help="bypass the once-per-day gate")
    args = ap.parse_args()
    run_daily(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
