#!/usr/bin/env python3
"""Poll Stripe /v1/events for 6 lifecycle event types and route each into Rick.

Extends the earlier checkout-only poller to a full revenue-event bridge:

    Stripe event type                     Rick event dispatched / workflow queued
    -------------------------------------  --------------------------------------
    checkout.session.completed            -> queue post_purchase_fulfillment
                                             + dispatch purchase_completed event
    invoice.payment_succeeded             -> dispatch renewal_confirmed event
    invoice.payment_failed                -> dispatch payment_failed event
    customer.subscription.deleted         -> dispatch subscription_cancelled event
    customer.subscription.trial_will_end  -> dispatch trial_expiring event
    charge.refunded                       -> dispatch charge_refunded event

Dispatched events are routed by `config/event-reactions.json` into the right
workflow (e.g. `subscription_cancelled -> queue tenant_retention`). This script
stays thin — policy is in the reactions config, not here.

State file at `~/rick-vault/operations/stripe-poll-state.json` tracks
`last_poll_timestamp` and `processed_event_ids` (kept to last 500).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Iterable

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
STATE_FILE = DATA_ROOT / "operations" / "stripe-poll-state.json"
ROOT_DIR = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT_DIR))

STRIPE_EVENTS_ENDPOINT = "https://api.stripe.com/v1/events"
EVENT_TYPES = (
    "checkout.session.completed",
    "invoice.payment_succeeded",
    "invoice.payment_failed",
    "customer.subscription.deleted",
    "customer.subscription.trial_will_end",
    "charge.refunded",
)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "last_poll_timestamp": 0,
        "processed_event_ids": [],
        # Retained for backward-compat with the old checkout-only state; unused here.
        "processed_session_ids": [],
    }


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _resolve_api_key() -> str:
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if key:
        return key
    env_file = ROOT_DIR / "config" / "rick.env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if line.startswith("STRIPE_SECRET_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def poll_stripe_events(api_key: str, since_timestamp: int) -> list[dict]:
    """Call /v1/events with created[gte] + types[] filter. Returns up to 50 events."""
    params: list[str] = [f"created[gte]={since_timestamp}", "limit=50"]
    for event_type in EVENT_TYPES:
        params.append(f"types[]={urllib.parse.quote(event_type)}")
    url = f"{STRIPE_EVENTS_ENDPOINT}?{'&'.join(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            events = data.get("data", [])
            return events if isinstance(events, list) else []
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"Stripe events API error: {exc}", file=sys.stderr)
        return []


def _extract_email(obj: dict) -> str:
    details = obj.get("customer_details") if isinstance(obj, dict) else None
    if isinstance(details, dict) and details.get("email"):
        return str(details.get("email", ""))
    for key in ("customer_email", "receipt_email", "email", "billing_email"):
        value = obj.get(key) if isinstance(obj, dict) else None
        if value:
            return str(value)
    return ""


def _extract_amount_cents(obj: dict) -> int:
    for key in ("amount_total", "amount_paid", "amount", "amount_refunded", "amount_due"):
        value = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(value, (int, float)) and value:
            return int(value)
    return 0


def _safe_metadata(obj: dict) -> dict:
    meta = obj.get("metadata") if isinstance(obj, dict) else None
    return meta if isinstance(meta, dict) else {}


def _make_event_payload(event: dict) -> dict:
    """Normalize a Stripe event into the payload we dispatch into Rick."""
    obj = event.get("data", {}).get("object", {}) if isinstance(event.get("data"), dict) else {}
    email = _extract_email(obj)
    amount_cents = _extract_amount_cents(obj)
    metadata = _safe_metadata(obj)
    return {
        "stripe_event_id": event.get("id", ""),
        "stripe_event_type": event.get("type", ""),
        "stripe_created": event.get("created", 0),
        "email": email,
        "amount_usd": amount_cents / 100.0 if amount_cents else 0.0,
        "customer_id": obj.get("customer") if isinstance(obj, dict) else None,
        "subscription_id": obj.get("subscription") if isinstance(obj, dict) else None,
        "session_id": obj.get("id") if (event.get("type") == "checkout.session.completed") else None,
        "metadata": metadata,
    }


def _dispatch_rick_event(conn, event_name: str, payload: dict) -> None:
    from runtime.engine import dispatch_event  # type: ignore
    dispatch_event(conn, None, None, event_name, payload)


def _handle_checkout_completed(conn, payload: dict) -> str | None:
    """Queue the existing post_purchase workflow for paid checkouts, then dispatch."""
    from runtime.engine import queue_post_purchase_workflow  # type: ignore
    email = payload.get("email") or ""
    if not email:
        return None
    delivery_url = (
        (payload.get("metadata") or {}).get("delivery_url")
        or os.environ.get("RICK_DEFAULT_DELIVERY_URL", "")
        or ""
    )
    wf_id = queue_post_purchase_workflow(
        conn,
        source_workflow_id=None,
        email=email,
        amount_usd=float(payload.get("amount_usd") or 0.0),
        delivery_url=delivery_url,
        source="stripe",
    )
    _dispatch_rick_event(conn, "purchase_completed", {**payload, "workflow_id": wf_id})
    # 2026-04-24: ALSO fire stripe_payment_succeeded so Noa (5-min activation
    # specialist) gets the event. Iris keeps purchase_completed (post-purchase
    # fulfillment); Noa runs the welcome+first-skill walkthrough. Disjoint
    # work — both should fire on every successful checkout.
    _dispatch_rick_event(conn, "stripe_payment_succeeded", {**payload, "workflow_id": wf_id})
    return wf_id


_RICK_EVENT_FOR_STRIPE: dict[str, str] = {
    "checkout.session.completed": "purchase_completed",
    "invoice.payment_succeeded": "renewal_confirmed",
    "invoice.payment_failed": "payment_failed",
    "customer.subscription.deleted": "subscription_cancelled",
    "customer.subscription.trial_will_end": "trial_expiring",
    "charge.refunded": "charge_refunded",
}


def _process_event(conn, event: dict) -> tuple[bool, str]:
    """Handle a single Stripe event. Returns (ok, description)."""
    event_type = event.get("type", "")
    payload = _make_event_payload(event)
    try:
        if event_type == "checkout.session.completed":
            wf_id = _handle_checkout_completed(conn, payload)
            return True, (
                f"{event_type} -> post_purchase_fulfillment {wf_id}" if wf_id
                else f"{event_type} (no email — skipped workflow, dispatched event only)"
            )
        rick_event = _RICK_EVENT_FOR_STRIPE.get(event_type)
        if not rick_event:
            return True, f"{event_type} (no mapping — skipped)"
        _dispatch_rick_event(conn, rick_event, payload)
        return True, f"{event_type} -> {rick_event}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{event_type} FAILED: {exc}"


def _runtime_connection():
    from runtime.db import connect, init_db  # type: ignore
    conn = connect()
    init_db(conn)
    return conn


def _filter_new_events(events: Iterable[dict], processed_ids: set[str]) -> list[dict]:
    out: list[dict] = []
    for event in events:
        eid = event.get("id")
        if not eid or eid in processed_ids:
            continue
        out.append(event)
    return out


def main() -> int:
    api_key = _resolve_api_key()
    if not api_key:
        print("No STRIPE_SECRET_KEY found. Skipping Stripe poll.", file=sys.stderr)
        return 0

    state = load_state()
    since = int(state.get("last_poll_timestamp") or 0)
    if since == 0:
        # First run: look back 24h so we don't miss anything recent.
        since = int(datetime.now().timestamp()) - 86400

    events = poll_stripe_events(api_key, since)
    if not events:
        save_state(state)
        print("No Stripe events in window.")
        return 0

    processed_ids_list = state.get("processed_event_ids") or []
    if not isinstance(processed_ids_list, list):
        processed_ids_list = []
    processed_ids = {str(x) for x in processed_ids_list}

    new_events = _filter_new_events(events, processed_ids)
    if not new_events:
        # Advance the poll timestamp so we don't keep re-fetching the same window.
        latest_created = max((int(e.get("created") or 0) for e in events), default=since)
        state["last_poll_timestamp"] = max(since, latest_created)
        state["processed_event_ids"] = processed_ids_list[-500:]
        save_state(state)
        print(f"All {len(events)} events already processed.")
        return 0

    try:
        conn = _runtime_connection()
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to connect to runtime DB: {exc}", file=sys.stderr)
        return 1

    processed_this_run: list[str] = []
    failures = 0
    try:
        for event in new_events:
            ok, description = _process_event(conn, event)
            print(description)
            if ok:
                processed_this_run.append(str(event.get("id", "")))
            else:
                failures += 1
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    # Persist state — advance timestamp + append processed event ids, cap to 500.
    latest_created = max((int(e.get("created") or 0) for e in events), default=since)
    state["last_poll_timestamp"] = max(since, latest_created)
    merged_ids = processed_ids_list + [eid for eid in processed_this_run if eid not in processed_ids]
    state["processed_event_ids"] = merged_ids[-500:]
    save_state(state)

    print(
        f"Processed {len(processed_this_run)} new event(s); {failures} failure(s). "
        f"{len(events) - len(new_events)} already-seen events skipped."
    )
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
