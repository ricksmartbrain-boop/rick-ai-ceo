#!/usr/bin/env python3
"""Poll Stripe /v1/events for 6 lifecycle event types and route each into Rick.

Extends the earlier checkout-only poller to a full revenue-event bridge:

    Stripe event type                     Rick event dispatched / workflow queued
    -------------------------------------  --------------------------------------
    checkout.session.completed            -> queue post_purchase_fulfillment
                                             + dispatch purchase_completed event
    invoice.payment_succeeded             -> cancel pending dunning items
                                             + dispatch renewal_confirmed event
    invoice.payment_failed                -> queue dunning episode (2 gated
                                             outbox emails max, deduped per
                                             (customer, invoice))
                                             + dispatch payment_failed event
    customer.subscription.deleted         -> cancel pending dunning items
                                             + dispatch subscription_cancelled event
    customer.subscription.trial_will_end  -> dispatch trial_expiring event
    charge.refunded                       -> dispatch charge_refunded event

Dispatched events are routed by `config/event-reactions.json` into the right
workflow (e.g. `subscription_cancelled -> queue tenant_retention`). This script
stays thin — policy is in the reactions config, not here.

2026-07-13 fulfillment-truth fixes:
  * Fulfillment delivery is resolved from `config/product-delivery-map.json`
    keyed by Stripe product ID (looked up via the checkout session's line
    items). Unknown product => loud ERROR log + skip. The old resolution
    (Stripe metadata or RICK_DEFAULT_DELIVERY_URL env) silently skipped
    delivery for every LinguaLive sale in June.
  * Failed events are never lost: last_poll_timestamp is held at the oldest
    failed event so the next poll re-fetches it.
  * Missing STRIPE_SECRET_KEY and processing failures exit nonzero with an
    ERROR log line so wrappers can alert (the old behavior was exit 0 +
    silent skip — dead for 2 weeks behind `|| true`).
  * NEW: read-only subscription-status sync (GET /v1/subscriptions filtered
    to RICK_REAL_PRODUCT_IDS). Detects status changes and
    cancel_at_period_end=true even with the webhook down, updates
    customers.status ('active'/'canceling'/'canceled'), appends
    customer_events rows, and prints `SUBSCRIPTION STATUS CHANGE:` lines the
    ops digest can pick up. Rick must never be churn-blind again.
  * `--dry-run` flag: Stripe GETs only — no engine dispatch, no DB writes,
    no state file writes.

2026-07-16 dunning machinery (involuntary churn — rodrigues_graciano lost
$15.98 LTV to a Mar-10 card failure with ZERO contact):
  * invoice.payment_failed on a Rick/LinguaLive product now queues a capped
    2-email fix-your-card episode through the gated outbox (day-0 + day-3
    reminder via send_after), linking the invoice's Stripe-hosted pay/update
    URL. Deduped per (customer, invoice) via an idempotent 'payment_failed'
    customer_events row. See the Dunning section below for why the hosted
    invoice URL and not the billing portal.
  * invoice.payment_succeeded / customer.subscription.deleted cancel any
    still-pending dunning items for that customer (never nag after recovery
    or after the sub is terminally gone), scoped to Rick products or the
    item's own invoice/subscription — other businesses' events on this
    shared Stripe account must not disarm a Rick episode.

State file at `~/rick-vault/operations/stripe-poll-state.json` tracks
`last_poll_timestamp`, `processed_event_ids` (kept to last 500), and
`subscription_statuses` (last known status per subscription id).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
STATE_FILE = DATA_ROOT / "operations" / "stripe-poll-state.json"
CANCEL_REASONS_FILE = DATA_ROOT / "churn" / "cancel-reasons.jsonl"
OUTBOX_DIR = DATA_ROOT / "mailbox" / "outbox"
DUNNING_REMINDER_DAYS = 3
# Max seconds _cancel_pending_dunning waits for a drain's in-flight
# .json.sending claim to release before scanning (bounded, no locking).
_CANCEL_CLAIM_WAIT_SECS = 4.0
ROOT_DIR = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT_DIR))

STRIPE_API_BASE = "https://api.stripe.com"
STRIPE_EVENTS_ENDPOINT = f"{STRIPE_API_BASE}/v1/events"
DELIVERY_MAP_FILE = ROOT_DIR / "config" / "product-delivery-map.json"
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
        "subscription_statuses": {},
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


def _stripe_get(api_key: str, path: str, params: dict | None = None) -> dict:
    """Read-only GET against the Stripe API. Raises on HTTP/parse errors."""
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(
        f"{STRIPE_API_BASE}{path}{query}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, dict) else {}


def load_delivery_map() -> dict | None:
    """Load {product_id: {name, delivery_kind, delivery_url}} or None on failure."""
    try:
        payload = json.loads(DELIVERY_MAP_FILE.read_text(encoding="utf-8"))
        products = payload.get("products")
        if isinstance(products, dict) and products:
            return products
        print(f"ERROR: {DELIVERY_MAP_FILE} has no 'products' map.", file=sys.stderr)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot load delivery map {DELIVERY_MAP_FILE}: {exc}", file=sys.stderr)
    return None


def poll_stripe_events(api_key: str, since_timestamp: int) -> list[dict] | None:
    """Call /v1/events with created[gte] + types[] filter, paginated.

    Returns the full window (bounded at 10 pages of 50, matching
    _list_rick_subscriptions) or None on API failure — None means "window
    state is unknown", and main() must fail loudly instead of concluding
    "no events".
    """
    base_params: list[str] = [f"created[gte]={since_timestamp}", "limit=50"]
    for event_type in EVENT_TYPES:
        base_params.append(f"types[]={urllib.parse.quote(event_type)}")
    events: list[dict] = []
    starting_after = ""
    for _page in range(10):
        params = list(base_params)
        if starting_after:
            params.append(f"starting_after={starting_after}")
        url = f"{STRIPE_EVENTS_ENDPOINT}?{'&'.join(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
            print(f"ERROR: Stripe events API error: {exc}", file=sys.stderr)
            return None
        page = data.get("data", [])
        if not isinstance(page, list):
            return events
        events.extend(page)
        if not data.get("has_more") or not page:
            return events
        starting_after = str(page[-1].get("id") or "")
        if not starting_after:
            return events
    print("WARNING: event window not fully drained after 10 pages.", file=sys.stderr)
    return events


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


def _session_product_ids(api_key: str, session_id: str) -> list[str]:
    """Product IDs on a checkout session's line items. Raises on API failure."""
    data = _stripe_get(
        api_key, f"/v1/checkout/sessions/{session_id}/line_items", {"limit": 10}
    )
    products: list[str] = []
    for item in data.get("data", []) or []:
        price = item.get("price") if isinstance(item, dict) else None
        product = price.get("product") if isinstance(price, dict) else None
        if isinstance(product, str) and product not in products:
            products.append(product)
    return products


def _handle_checkout_completed(conn, payload: dict, api_key: str, delivery_map: dict) -> str:
    """Queue post_purchase fulfillment using the per-product delivery map.

    Unknown product => loud ERROR log + skip (never silent, never the wrong
    product's welcome email). Raises on Stripe API failure so the event is
    retried next poll.
    """
    from runtime.engine import queue_post_purchase_workflow  # type: ignore
    email = payload.get("email") or ""
    if not email:
        print(
            f"ERROR: checkout {payload.get('session_id') or '<no-id>'} has no "
            f"customer email — fulfillment impossible.",
            file=sys.stderr,
        )
        return "checkout.session.completed (skipped: no email, nothing dispatched)"

    session_id = str(payload.get("session_id") or "")
    products = _session_product_ids(api_key, session_id) if session_id else []
    entry = None
    matched_product = ""
    for product_id in products:
        candidate = delivery_map.get(product_id)
        if isinstance(candidate, dict):
            entry = candidate
            matched_product = product_id
            break
    if entry is None:
        print(
            f"ERROR: UNKNOWN PRODUCT on checkout {session_id} "
            f"(products={products or ['<none>']}, email={email}, "
            f"amount=${payload.get('amount_usd')}). Not in "
            f"{DELIVERY_MAP_FILE.name} — fulfillment SKIPPED, add a mapping.",
            file=sys.stderr,
        )
        return f"checkout.session.completed (unknown product {products} — fulfillment skipped LOUDLY)"

    # Explicit per-session metadata still wins; the map is the default.
    delivery_url = (
        (payload.get("metadata") or {}).get("delivery_url")
        or str(entry.get("delivery_url") or "")
    )
    wf_id = queue_post_purchase_workflow(
        conn,
        source_workflow_id=None,
        email=email,
        payment_id=session_id,
        amount_usd=float(payload.get("amount_usd") or 0.0),
        delivery_url=delivery_url,
        source="stripe",
        product_name=str(entry.get("name") or ""),
    )
    enriched = {
        **payload,
        "workflow_id": wf_id,
        "product_id": matched_product,
        "product_name": str(entry.get("name") or ""),
        "delivery_kind": str(entry.get("delivery_kind") or ""),
        "delivery_url": delivery_url,
    }
    _dispatch_rick_event(conn, "purchase_completed", enriched)
    # 2026-04-24: ALSO fire stripe_payment_succeeded so Noa (5-min activation
    # specialist) gets the event. Iris keeps purchase_completed (post-purchase
    # fulfillment); Noa runs the welcome+first-skill walkthrough. Disjoint
    # work — both should fire on every successful checkout.
    _dispatch_rick_event(conn, "stripe_payment_succeeded", enriched)
    return (
        f"checkout.session.completed [{entry.get('name') or matched_product}] "
        f"-> post_purchase_fulfillment {wf_id}"
    )


# --- Dunning (involuntary-churn) machinery, 2026-07-16 -----------------------
#
# invoice.payment_failed used to be dispatched into the engine and dropped
# (event-reactions.json has no 'payment_failed' reaction) — rodrigues_graciano
# churned 2026-03 after a card failure with zero contact ever. Detection point
# is THIS poll: the meetrick-api webhook's handlePaymentFailed only flips its
# own Railway Postgres row ('past_due') and console.logs — nothing local can
# read it — and the subscription-status sync below sees 'past_due' without
# invoice granularity, so it cannot dedupe per failure episode.
#
# Link choice (verified read-only 2026-07-16 against the live account): the
# billing-portal configuration bpc_1KtVgm… exists but has
# payment_method_update=False — a customer cannot fix a card there, and portal
# sessions would need a Stripe POST at send time. invoice.hosted_invoice_url
# is present on real failed invoices (checked rodrigues's in_1T9W0G…, status
# 'open') and lets the customer pay the open invoice with a new card in one
# step, no Stripe write needed. So: hosted_invoice_url.
#
# Episode = (customer, invoice). Hard cap 2 emails: day-0 'your payment
# failed' + day-3 reminder queued up-front with send_after (the outbox drain
# honors it); recovery or terminal cancel flips still-pending items to
# 'cancelled'. The customer_events 'payment_failed' row is the idempotency
# record — Stripe retries the same invoice repeatedly (rodrigues's invoice
# shows attempt_count=5) and each retry emits a fresh event that must NOT
# start a second episode. Shared Stripe account: invoices for non-Rick
# products are skipped quietly (they belong to Vlad's other businesses).
# Transactional send policy: dunning auto-sends through the gated pipeline
# (is_send_allowed + channel state in email-send-outbox.py), like access
# delivery — NOT held_pending_owner.


def _invoice_product_ids(invoice: dict) -> list[str]:
    """Product ids on an invoice's line items, across Stripe API shapes.

    This account renders invoices with the newer shape where the product sits
    at lines.data[].pricing.price_details.product (verified live 2026-07-16);
    price.product / plan.product are kept for older-shaped events.
    """
    products: list[str] = []
    lines = invoice.get("lines") if isinstance(invoice, dict) else None
    rows = lines.get("data") if isinstance(lines, dict) else None
    for line in rows or []:
        if not isinstance(line, dict):
            continue
        price = line.get("price") if isinstance(line.get("price"), dict) else {}
        plan = line.get("plan") if isinstance(line.get("plan"), dict) else {}
        pricing = line.get("pricing") if isinstance(line.get("pricing"), dict) else {}
        details = pricing.get("price_details") if isinstance(pricing.get("price_details"), dict) else {}
        for candidate in (price.get("product"), plan.get("product"), details.get("product")):
            if isinstance(candidate, str) and candidate not in products:
                products.append(candidate)
    return products


def _subscription_product_ids(sub: dict) -> list[str]:
    """Product ids on a subscription's items (price.product, matching
    _list_rick_subscriptions; plan.product kept for older-shaped events)."""
    products: list[str] = []
    items = sub.get("items") if isinstance(sub, dict) else None
    rows = items.get("data") if isinstance(items, dict) else None
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        price = item.get("price") if isinstance(item.get("price"), dict) else {}
        plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
        for candidate in (price.get("product"), plan.get("product")):
            if isinstance(candidate, str) and candidate not in products:
                products.append(candidate)
    return products


def _dunning_episode_exists(conn, customer_id: str, invoice_id: str) -> bool:
    """True if a payment_failed customer_event for this invoice already exists."""
    rows = conn.execute(
        "SELECT payload_json FROM customer_events "
        "WHERE customer_id = ? AND event_type = 'payment_failed'",
        (customer_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("invoice_id") == invoice_id:
            return True
    return False


def _dunning_bodies(first_name: str, product_name: str, amount_usd: float, pay_url: str) -> tuple[str, str]:
    """Day-0 + day-3 dunning email bodies. Plain ASCII on purpose — Don Roth's
    access email was bounced by duck.com for unicode-in-headers (2026-07-13)."""
    author = os.getenv("RICK_PUBLIC_AUTHOR", "Rick")
    amount = f"${amount_usd:.2f}" if amount_usd else "latest"
    day0 = (
        f"**Subject:** Your {product_name} payment did not go through\n\n"
        f"Hi {first_name},\n\n"
        f"Quick heads-up: the {amount} charge for {product_name} did not go\n"
        f"through. Cards expire and banks get cautious -- it happens.\n\n"
        f"You can pay the open invoice and update your card in one step here:\n\n"
        f"{pay_url}\n\n"
        f"Your access stays active for now and Stripe will retry automatically,\n"
        f"but the link above fixes it fastest. If anything looks off, just\n"
        f"reply to this email.\n\n"
        f"-- {author}\n"
    )
    day3 = (
        f"**Subject:** Reminder: {product_name} payment still needs a fix\n\n"
        f"Hi {first_name},\n\n"
        f"A few days ago the {amount} charge for {product_name} failed, and it\n"
        f"still has not gone through. To keep your access, you can pay the open\n"
        f"invoice and update your card here (takes about a minute):\n\n"
        f"{pay_url}\n\n"
        f"If you meant to cancel instead -- no hard feelings, just reply and I\n"
        f"will take care of it.\n\n"
        f"-- {author}\n"
    )
    return day0, day3


def _handle_payment_failed(conn, event: dict, api_key: str, delivery_map: dict) -> str:
    """Queue the capped 2-email dunning episode for a failed Rick invoice.

    Idempotent per (customer, invoice): the customer_events row is committed
    BEFORE the outbox files are written (fail-safe comment below), and stops
    every later Stripe retry of the same invoice from starting a second
    episode.
    """
    obj = event.get("data", {}).get("object", {}) if isinstance(event.get("data"), dict) else {}
    invoice_id = str(obj.get("id") or "")
    email = _extract_email(obj)

    from runtime.revenue_signals import RICK_REAL_PRODUCT_IDS  # type: ignore
    products = _invoice_product_ids(obj)
    matched = [p for p in products if p in RICK_REAL_PRODUCT_IDS]
    if not matched:
        # Shared Stripe account — other businesses' failed invoices are normal here.
        return (
            f"invoice.payment_failed {invoice_id or '<no-id>'} "
            f"(non-Rick products {products or ['<none>']} — no dunning)"
        )
    if not invoice_id or not email:
        print(
            f"ERROR: payment_failed invoice={invoice_id or '<no-id>'} "
            f"email={email or '<none>'} — dunning impossible.",
            file=sys.stderr,
        )
        return f"invoice.payment_failed (missing invoice id or email — dunning skipped LOUDLY)"

    row = conn.execute(
        "SELECT id, name FROM customers WHERE lower(email) = lower(?)", (email,)
    ).fetchone()
    if row is None:
        print(
            f"ERROR: payment_failed for {email} ({invoice_id}) has no customers row — "
            f"dunning skipped, backfill needed.",
            file=sys.stderr,
        )
        return f"invoice.payment_failed {invoice_id} (no customers row for {email} — dunning skipped LOUDLY)"
    if _dunning_episode_exists(conn, row["id"], invoice_id):
        return f"invoice.payment_failed {invoice_id} (episode already recorded — dedupe, nothing queued)"

    pay_url = str(obj.get("hosted_invoice_url") or "")
    if not pay_url:
        # Transient lookup failures raise so the poll window holds + retries.
        pay_url = str(
            _stripe_get(api_key, f"/v1/invoices/{invoice_id}").get("hosted_invoice_url") or ""
        )
    if not pay_url:
        print(
            f"ERROR: invoice {invoice_id} has no hosted_invoice_url — dunning email "
            f"would have no fix link, skipped.",
            file=sys.stderr,
        )
        return f"invoice.payment_failed {invoice_id} (no hosted_invoice_url — dunning skipped LOUDLY)"

    entry = next(
        (delivery_map.get(p) for p in matched if isinstance(delivery_map.get(p), dict)), None
    )
    product_name = str((entry or {}).get("name") or "your subscription")
    first_name = (str(row["name"] or "").split() or ["there"])[0]
    amount_due = obj.get("amount_due")
    amount_usd = (amount_due / 100.0) if isinstance(amount_due, (int, float)) else 0.0
    parent = obj.get("parent") if isinstance(obj.get("parent"), dict) else {}
    sub_details = (
        parent.get("subscription_details")
        if isinstance(parent.get("subscription_details"), dict)
        else {}
    )
    sub_id = str(obj.get("subscription") or sub_details.get("subscription") or "")

    from runtime.engine import slugify_email  # type: ignore
    now = datetime.now()
    day0_body, day3_body = _dunning_bodies(first_name, product_name, amount_usd, pay_url)
    base = f"dunning-{slugify_email(email)}-{invoice_id}"
    email_plan = (
        ("day0", "dunning", day0_body, ""),
        (
            f"day{DUNNING_REMINDER_DAYS}",
            "dunning-reminder",
            day3_body,
            (now + timedelta(days=DUNNING_REMINDER_DAYS)).isoformat(timespec="seconds"),
        ),
    )
    outbox_files = [f"{base}-{suffix}.json" for suffix, _, _, _ in email_plan]

    # Commit the dedupe row FIRST, write outbox files SECOND — deliberate
    # fail-safe direction: a crash between the two leaves the episode
    # marked-but-unsent (at most one lost nag, spottable via outbox_files in
    # the committed payload). The old file-first order re-sent 'payment
    # failed' roughly hourly whenever the commit failed after the drain had
    # already consumed the day-0 file.
    conn.execute(
        "INSERT INTO customer_events (id, customer_id, workflow_id, event_type, payload_json, created_at) "
        "VALUES (?, ?, NULL, 'payment_failed', ?, ?)",
        (
            f"evt_{uuid.uuid4().hex[:12]}",
            row["id"],
            json.dumps({
                "invoice_id": invoice_id,
                "subscription_id": sub_id,
                "amount_usd": amount_usd,
                "hosted_invoice_url": pay_url,
                "outbox_files": outbox_files,
                "source": "stripe-poll",
            }),
            now.isoformat(timespec="seconds"),
        ),
    )
    conn.commit()

    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    sent_dir = OUTBOX_DIR.parent / "sent"
    queued: list[str] = []
    for (_suffix, item_type, body, send_after), name in zip(email_plan, outbox_files):
        path = OUTBOX_DIR / name
        if (
            path.exists()
            or (sent_dir / name).exists()
            or path.with_name(name + ".sending").exists()
        ):
            # Never resurrect an existing item (sent/cancelled/pending, or a
            # drain's in-flight .sending claim) as a fresh 'pending' — an
            # overlapping poll must not double-send.
            print(f"DUNNING SKIP: {name} already exists — not re-queued.", file=sys.stderr)
            continue
        item = {
            "to": email,
            "status": "pending",
            "type": item_type,
            "cold": False,
            "invoice_id": invoice_id,
            "subscription_id": sub_id,
            "product": product_name,
            "body_markdown": body,
            "created_at": now.isoformat(timespec="seconds"),
        }
        if send_after:
            item["send_after"] = send_after
        path.write_text(json.dumps(item, indent=2) + "\n", encoding="utf-8")
        queued.append(name)
    if not queued:
        # Overlapping-poll re-run: every episode file already existed. Say so
        # — a 'DUNNING QUEUED:' line here would double-count the episode
        # start in the ops digest, and the description must match reality.
        line = (
            f"DUNNING ALREADY QUEUED: {email} invoice {invoice_id} — all "
            f"episode files already exist, nothing re-queued"
        )
        print(line)
        print(line, file=sys.stderr)
        return f"invoice.payment_failed {invoice_id} (dunning already queued — nothing new written)"
    # stderr on purpose: the runners drop stdout but keep stderr in
    # logs/cron/stripe-poll.err.log — the ops digest must see dunning starts.
    line = (
        f"DUNNING QUEUED: {email} invoice {invoice_id} (${amount_usd:.2f} "
        f"{product_name}) day-0 + day-{DUNNING_REMINDER_DAYS} reminder"
    )
    print(line)
    print(line, file=sys.stderr)
    return f"invoice.payment_failed -> dunning queued ({', '.join(queued)})"


def _cancel_pending_dunning(
    email: str,
    *,
    cancelled_by: str,
    rick_product: bool,
    invoice_id: str = "",
    subscription_id: str = "",
) -> int:
    """Flip still-pending dunning outbox items for this address to cancelled.

    Runs on invoice.payment_succeeded (card fixed) and
    customer.subscription.deleted (nothing left to dun) — a 'please update
    your card' reminder must never land after the episode is over. Scoped on
    purpose (shared Stripe account, ~50 businesses): an item is cancelled
    only when the triggering event is for a Rick product (rick_product) OR
    references the item's own invoice/subscription — a customer paying one
    of Vlad's OTHER businesses must not silently disarm a still-open Rick
    dunning episode. Exact-episode match is kept as a fallback because
    older-shaped events can carry no line-item product data.

    Claim-aware (2026-07-17): a drain holds an item as <name>.json.sending
    for the seconds around its Resend call. This event fires once-only
    (processed_event_ids never retries), so a glob that misses the claimed
    file skips the cancel FOREVER and the day-3 nag can land after
    recovery. Bounded retry — wait briefly for in-flight dunning claims to
    release (back to .json on a block, or into sent/ after a send) before
    scanning; if a claim outlives the wait, log loudly. No locking
    framework on purpose.
    """
    if not email or not OUTBOX_DIR.exists():
        return 0
    cancelled = 0
    now_iso = datetime.now().isoformat(timespec="seconds")
    deadline = time.monotonic() + _CANCEL_CLAIM_WAIT_SECS
    while any(OUTBOX_DIR.glob("dunning-*.json.sending")) and time.monotonic() < deadline:
        time.sleep(0.25)
    leftover = sorted(p.name for p in OUTBOX_DIR.glob("dunning-*.json.sending"))
    if leftover:
        line = (
            f"DUNNING CANCEL RACE: claim(s) still held after "
            f"{_CANCEL_CLAIM_WAIT_SECS:.0f}s — {', '.join(leftover)} cannot "
            f"be cancelled by this once-only event ({cancelled_by}); check "
            f"outbox/sent manually."
        )
        print(line)
        print(line, file=sys.stderr)
    for path in sorted(OUTBOX_DIR.glob("dunning-*.json")):
        try:
            msg = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(msg, dict) or msg.get("status") != "pending":
            continue
        if str(msg.get("to") or "").strip().lower() != email.strip().lower():
            continue
        same_episode = bool(
            (invoice_id and msg.get("invoice_id") == invoice_id)
            or (subscription_id and msg.get("subscription_id") == subscription_id)
        )
        if not rick_product and not same_episode:
            line = (
                f"DUNNING CANCEL SKIPPED: {path.name} — {cancelled_by} is for a "
                f"non-Rick product and a different invoice/subscription."
            )
            print(line)
            print(line, file=sys.stderr)
            continue
        msg["status"] = "cancelled"
        msg["cancelled_at"] = now_iso
        msg["cancelled_by"] = cancelled_by
        path.write_text(json.dumps(msg, indent=2) + "\n", encoding="utf-8")
        # Post-write verify: if a drain claimed the file between our read and
        # write, our write_text recreated the .json beside the claim and the
        # drain's release/settle rename can overwrite the cancel — sub-second
        # window, but the leaked nag is customer-facing, so never silent.
        sending_sibling = path.with_name(path.name + ".sending")
        if not path.exists() or sending_sibling.exists():
            line = (
                f"DUNNING CANCEL RACE: {path.name} was claimed mid-cancel — "
                f"cancelled status may be overwritten; check outbox/sent "
                f"({cancelled_by})"
            )
            print(line)
            print(line, file=sys.stderr)
        cancelled += 1
        line = f"DUNNING CANCELLED: {path.name} ({cancelled_by})"
        print(line)
        print(line, file=sys.stderr)
    return cancelled


_RICK_EVENT_FOR_STRIPE: dict[str, str] = {
    "checkout.session.completed": "purchase_completed",
    "invoice.payment_succeeded": "renewal_confirmed",
    "invoice.payment_failed": "payment_failed",
    "customer.subscription.deleted": "subscription_cancelled",
    "customer.subscription.trial_will_end": "trial_expiring",
    "charge.refunded": "charge_refunded",
}


def _process_event(conn, event: dict, api_key: str, delivery_map: dict) -> tuple[bool, str]:
    """Handle a single Stripe event. Returns (ok, description)."""
    event_type = event.get("type", "")
    payload = _make_event_payload(event)
    try:
        if event_type == "checkout.session.completed":
            return True, _handle_checkout_completed(conn, payload, api_key, delivery_map)
        if event_type == "invoice.payment_failed":
            description = _handle_payment_failed(conn, event, api_key, delivery_map)
            _dispatch_rick_event(conn, "payment_failed", payload)
            return True, description
        if event_type in ("invoice.payment_succeeded", "customer.subscription.deleted"):
            # Subscription objects carry no email — resolve via the customer id.
            email = payload.get("email") or _customer_email(
                api_key, str(payload.get("customer_id") or ""), {}
            )
            from runtime.revenue_signals import RICK_REAL_PRODUCT_IDS  # type: ignore
            obj = event.get("data", {}).get("object", {}) if isinstance(event.get("data"), dict) else {}
            if event_type == "invoice.payment_succeeded":
                products = _invoice_product_ids(obj)
                inv_id = str(obj.get("id") or "")
                parent = obj.get("parent") if isinstance(obj.get("parent"), dict) else {}
                sub_details = (
                    parent.get("subscription_details")
                    if isinstance(parent.get("subscription_details"), dict)
                    else {}
                )
                sub_id = str(obj.get("subscription") or sub_details.get("subscription") or "")
            else:
                products = _subscription_product_ids(obj)
                inv_id = ""
                sub_id = str(obj.get("id") or "")
            _cancel_pending_dunning(
                str(email or ""),
                cancelled_by=event_type,
                rick_product=any(p in RICK_REAL_PRODUCT_IDS for p in products),
                invoice_id=inv_id,
                subscription_id=sub_id,
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


# --- Subscription-status sync (read-only against Stripe) ---------------------


def _fmt_ts(value) -> str:
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d") if value else ""
    except (ValueError, TypeError, OSError):
        return ""


def _effective_sub_status(sub: dict) -> tuple[str, str]:
    """Map a Stripe subscription to (rick_status, end_date). rick_status is
    'canceled', 'canceling', or the raw Stripe status ('active', 'past_due', ...)."""
    status = str(sub.get("status") or "unknown")
    if status == "canceled":
        return "canceled", _fmt_ts(sub.get("ended_at") or sub.get("canceled_at"))
    if sub.get("cancel_at_period_end"):
        return "canceling", _fmt_ts(sub.get("cancel_at") or sub.get("current_period_end"))
    return status, ""


def _list_rick_subscriptions(api_key: str) -> list[dict]:
    """All subscriptions (any status) whose line items hit RICK_REAL_PRODUCT_IDS."""
    from runtime.revenue_signals import RICK_REAL_PRODUCT_IDS  # type: ignore
    subs: list[dict] = []
    starting_after = None
    for _ in range(10):
        params: dict = {"status": "all", "limit": 100}
        if starting_after:
            params["starting_after"] = starting_after
        data = _stripe_get(api_key, "/v1/subscriptions", params)
        rows = data.get("data", []) or []
        for sub in rows:
            items = (sub.get("items") or {}).get("data") or []
            products = {
                (item.get("price") or {}).get("product")
                for item in items
                if isinstance(item, dict)
            }
            if products & RICK_REAL_PRODUCT_IDS:
                subs.append(sub)
        if not data.get("has_more") or not rows:
            break
        starting_after = rows[-1].get("id")
    return subs


def _customer_email(api_key: str, customer_id: str, cache: dict) -> str:
    if not customer_id:
        return ""
    if customer_id not in cache:
        try:
            cache[customer_id] = str(
                _stripe_get(api_key, f"/v1/customers/{customer_id}").get("email") or ""
            )
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: customer lookup {customer_id} failed: {exc}", file=sys.stderr)
            cache[customer_id] = ""
    return cache[customer_id]


def harvest_cancellation_details(
    api_key: str,
    conn,
    sub: dict,
    effective: str,
    end_date: str,
    email_cache: dict,
    *,
    dry_run: bool,
    source: str = "stripe-poll",
) -> bool:
    """Persist Stripe cancellation_details for a canceled/canceling sub — once.

    2026-07-16: cancel reasons used to evaporate (never harvested from Stripe,
    never stored). Record feedback/comment/reason into customer_events
    (event_type=cancel_reason) + churn/cancel-reasons.jsonl. All-None details
    are recorded explicitly as 'none' so "checked, survey empty" (Diane) is
    distinguishable from "never harvested". Idempotent via details_sig: an
    existing cancel_reason event with the same subscription_id + signature
    means done — but a LATER survey answer (new signature) records again.
    Returns True only when a new record was written.
    """
    sub_id = str(sub.get("id") or "")
    details = sub.get("cancellation_details")
    if not isinstance(details, dict):
        details = {}
    feedback = str(details.get("feedback") or "none")
    comment = str(details.get("comment") or "none")
    reason = str(details.get("reason") or "none")
    details_sig = f"feedback={feedback}|comment={comment}|reason={reason}"

    if conn is not None:
        # Exact json_extract match. The earlier LIKE-on-payload_json check
        # compared the RAW sig against ensure_ascii-escaped JSON, so any
        # comment with non-ASCII/quotes/newlines never matched and the sub
        # re-recorded every 30-min poll, unbounded (2026-07-16 review blocker).
        already = conn.execute(
            "SELECT 1 FROM customer_events WHERE event_type = 'cancel_reason' "
            "AND json_extract(payload_json, '$.subscription_id') = ? "
            "AND json_extract(payload_json, '$.details_sig') = ? LIMIT 1",
            (sub_id, details_sig),
        ).fetchone()
        if already:
            return False
    if dry_run:
        print(f"DRY-RUN would record cancel reason for {sub_id}: {details_sig}")
        return False

    email = _customer_email(api_key, str(sub.get("customer") or ""), email_cache)
    if not email:
        print(
            f"ERROR: cancel-reason harvest: no email for {sub_id} — will retry next poll.",
            file=sys.stderr,
        )
        return False
    row = conn.execute(
        "SELECT id FROM customers WHERE lower(email) = lower(?)", (email,)
    ).fetchone()
    if row is None:
        print(
            f"ERROR: cancel-reason harvest: {email} ({sub_id}) has no customers row — "
            f"backfill needed.",
            file=sys.stderr,
        )
        return False
    now_iso = datetime.now().isoformat(timespec="seconds")
    rec = {
        "ts": now_iso,
        "customer": email,
        "customer_id": row["id"],
        "source": source,
        "subscription_id": sub_id,
        "subscription_status": effective,
        "end_date": end_date,
        "feedback": feedback,
        "comment": comment,
        "reason": reason,
        "details_sig": details_sig,
        "tag": "churn_feedback",
    }
    conn.execute(
        "INSERT INTO customer_events (id, customer_id, workflow_id, event_type, payload_json, created_at) "
        "VALUES (?, ?, NULL, 'cancel_reason', ?, ?)",
        (f"evt_{uuid.uuid4().hex[:12]}", row["id"], json.dumps(rec), now_iso),
    )
    conn.commit()
    CANCEL_REASONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CANCEL_REASONS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"CANCEL REASON recorded: {email} {sub_id} {details_sig}")
    return True


def sync_subscription_statuses(api_key: str, conn, state: dict, *, dry_run: bool) -> int:
    """Reconcile Stripe subscription statuses into customers/customer_events.

    Catches cancellations (incl. cancel_at_period_end) even with the webhook
    down. Returns the number of status changes detected.
    """
    known = state.get("subscription_statuses")
    if not isinstance(known, dict):
        known = {}
    subs = _list_rick_subscriptions(api_key)
    email_cache: dict = {}
    now_iso = datetime.now().isoformat(timespec="seconds")
    changes = 0

    for sub in subs:
        sub_id = str(sub.get("id") or "")
        if not sub_id:
            continue
        effective, end_date = _effective_sub_status(sub)
        # Cancel-reason harvest runs every poll (not only on status change):
        # idempotent, and a survey answer arriving later must still land.
        if effective in ("canceled", "canceling"):
            try:
                harvest_cancellation_details(
                    api_key, conn, sub, effective, end_date, email_cache,
                    dry_run=dry_run,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"ERROR: cancel-reason harvest failed for {sub_id}: {exc}",
                    file=sys.stderr,
                )
        previous = known.get(sub_id)
        if previous == effective:
            continue
        changes += 1
        email = _customer_email(api_key, str(sub.get("customer") or ""), email_cache)
        ends = f" (ends {end_date})" if end_date else ""
        # stderr on purpose: run-heartbeat.sh/run-nightly.sh send stdout to
        # /dev/null but keep stderr in logs/cron/stripe-poll.err.log — churn
        # signals must reach the ops digest.
        line = (
            f"SUBSCRIPTION STATUS CHANGE: {email or '<no-email>'} {sub_id} "
            f"{previous or 'untracked'} -> {effective}{ends}"
        )
        print(line)
        print(line, file=sys.stderr)
        if dry_run:
            continue

        if not email:
            # Do NOT record in known: email lookup can fail transiently and
            # the status change must be retried on the next poll.
            print(f"ERROR: no customer email for {sub_id}; local DB not updated — will retry.", file=sys.stderr)
            continue
        row = conn.execute(
            "SELECT id, status, metadata_json FROM customers WHERE lower(email) = lower(?)",
            (email,),
        ).fetchone()
        if row is None:
            # Auto-backfill (2026-07-18): the webhook path can fulfill+enroll
            # before any customers row exists (vojta), and the old ERROR line
            # left the sale invisible to the churn guard, renewal gates, and
            # the day-14 scoreboard — with nothing ever retrying. Everything
            # a row needs is in hand; create it loudly.
            stamp = datetime.now().isoformat(timespec="seconds")
            backfill_id = f"cus_{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO customers (id, email, name, source, latest_workflow_id, status, "
                "tags_json, metadata_json, created_at, updated_at, last_seen_at) "
                "VALUES (?, ?, '', 'stripe-poll-backfill', NULL, ?, '[]', ?, ?, ?, ?)",
                (
                    backfill_id,
                    email.strip().lower(),
                    effective,
                    json.dumps({"subscription_id": sub_id, "end_date": end_date or "", "backfilled_at": stamp}),
                    stamp, stamp, stamp,
                ),
            )
            conn.commit()
            known[sub_id] = effective
            print(
                f"BACKFILLED: customers row {backfill_id} created for {email} "
                f"({sub_id}, {effective}) — row was missing, sale was invisible locally.",
                file=sys.stderr,
            )
            continue
        if row["status"] == effective:
            # Local row already reflects the truth (e.g. manual backfill) —
            # start tracking without appending a duplicate event.
            known[sub_id] = effective
            continue
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
            if not isinstance(metadata, dict):
                metadata = {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        metadata["stripe_subscription_id"] = sub_id
        metadata["stripe_subscription_status"] = effective
        metadata["status_note"] = f"stripe-poll {now_iso}: {effective}{ends}"
        conn.execute(
            "UPDATE customers SET status = ?, metadata_json = ?, updated_at = ? WHERE id = ?",
            (effective, json.dumps(metadata), now_iso, row["id"]),
        )
        conn.execute(
            "INSERT INTO customer_events (id, customer_id, workflow_id, event_type, payload_json, created_at) "
            "VALUES (?, ?, NULL, 'subscription_status_changed', ?, ?)",
            (
                f"evt_{uuid.uuid4().hex[:12]}",
                row["id"],
                json.dumps({
                    "subscription_id": sub_id,
                    "old_status": row["status"],
                    "new_status": effective,
                    "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
                    "end_date": end_date,
                    "source": "stripe-poll",
                }),
                now_iso,
            ),
        )
        conn.commit()
        # Only after a successful commit — a transient failure above must
        # leave the change un-recorded so the next poll retries it.
        known[sub_id] = effective

    if not dry_run:
        state["subscription_statuses"] = known
    if changes == 0:
        print(f"Subscription sync: {len(subs)} Rick/LinguaLive sub(s), no status changes.")
    return changes


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    api_key = _resolve_api_key()
    if not api_key:
        print("ERROR: No STRIPE_SECRET_KEY found — Stripe poll DID NOT RUN.", file=sys.stderr)
        return 1

    delivery_map = load_delivery_map()

    state = load_state()
    since = int(state.get("last_poll_timestamp") or 0)
    if since == 0:
        # First run: look back 24h so we don't miss anything recent.
        since = int(datetime.now().timestamp()) - 86400

    events = poll_stripe_events(api_key, since)
    if events is None:
        # API failure — window state unknown. Exit hard (skip state save) so
        # the run-heartbeat/run-nightly wrappers log '[error] stripe-poll
        # FAILED' instead of a silent forever-"No events" success.
        print("ERROR: Stripe events poll failed — window NOT advanced.", file=sys.stderr)
        return 2

    processed_ids_list = state.get("processed_event_ids") or []
    if not isinstance(processed_ids_list, list):
        processed_ids_list = []
    processed_ids = {str(x) for x in processed_ids_list}
    new_events = _filter_new_events(events, processed_ids)
    # /v1/events returns newest-first; process chronologically so a recovery
    # (invoice.payment_succeeded) that lands in the same window as an older
    # payment failure cancels the dunning items AFTER they are queued, not
    # before they exist.
    new_events.sort(key=lambda e: int(e.get("created") or 0))

    conn = None
    if not dry_run:
        try:
            conn = _runtime_connection()
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: Failed to connect to runtime DB: {exc}", file=sys.stderr)
            return 1

    processed_this_run: list[str] = []
    failed_events: list[dict] = []
    if not events:
        print("No Stripe events in window.")
    elif not new_events:
        print(f"All {len(events)} events already processed.")
    elif dry_run:
        for event in new_events:
            event_type = event.get("type", "")
            desc = f"DRY-RUN would process {event_type} {event.get('id')}"
            if event_type == "checkout.session.completed" and delivery_map:
                payload = _make_event_payload(event)
                try:
                    products = _session_product_ids(api_key, str(payload.get("session_id") or ""))
                except Exception as exc:  # noqa: BLE001
                    products = [f"<lookup failed: {exc}>"]
                mapped = [p for p in products if p in delivery_map]
                desc += f" products={products} mapped={mapped or 'NONE (would skip LOUDLY)'}"
            print(desc)
    else:
        if delivery_map is None and any(
            e.get("type") == "checkout.session.completed" for e in new_events
        ):
            # No map = cannot fulfill safely. Treat checkouts as failed so the
            # window does not advance past them; process the rest.
            print("ERROR: delivery map unavailable — checkout events held for retry.", file=sys.stderr)
        for event in new_events:
            if delivery_map is None and event.get("type") == "checkout.session.completed":
                failed_events.append(event)
                continue
            ok, description = _process_event(conn, event, api_key, delivery_map or {})
            print(description)
            if ok:
                processed_this_run.append(str(event.get("id", "")))
            else:
                failed_events.append(event)

    # --- Subscription-status sync (runs every poll, webhook-independent) ---
    sync_failed = False
    try:
        sync_subscription_statuses(api_key, conn, state, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        sync_failed = True
        print(f"ERROR: subscription status sync failed: {exc}", file=sys.stderr)

    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    failures = len(failed_events)
    if dry_run:
        print(
            f"DRY-RUN complete: {len(new_events)} new event(s) inspected, "
            f"no state/DB/engine writes."
        )
        return 0

    # Persist state — advance timestamp + append processed event ids, cap to 500.
    # NEVER advance past a failed event: hold the window at the oldest failure
    # so it is re-fetched next poll (successes are deduped via processed ids).
    if failed_events:
        state["last_poll_timestamp"] = min(
            int(e.get("created") or since) for e in failed_events
        )
    else:
        latest_created = max((int(e.get("created") or 0) for e in events), default=since)
        state["last_poll_timestamp"] = max(since, latest_created)
    merged_ids = processed_ids_list + [eid for eid in processed_this_run if eid not in processed_ids]
    state["processed_event_ids"] = merged_ids[-500:]
    save_state(state)

    if events:
        print(
            f"Processed {len(processed_this_run)} new event(s); {failures} failure(s). "
            f"{len(events) - len(new_events)} already-seen events skipped."
        )
    if failures:
        print(
            f"ERROR: {failures} Stripe event(s) FAILED; poll window held at "
            f"{state['last_poll_timestamp']} for retry.",
            file=sys.stderr,
        )
    return 0 if (failures == 0 and not sync_failed) else 2


if __name__ == "__main__":
    raise SystemExit(main())
