#!/usr/bin/env python3
"""
reply-triage.py — scan inbound replies across channels and route hot ones.

Checks:
  (a) inbound email rows from the imap-watcher triage JSONL
      (~/rick-vault/mailbox/triage/inbound-YYYY-MM-DD.jsonl, today + yesterday).
      2026-07-13: replaced the himalaya UNSEEN scan — imap-watcher is the ONLY
      IMAP consumer now; it marks mail \\Seen within 10 min, which permanently
      starved this script (call-queue.jsonl empty since Jun 11). Downstream
      consumers read triage, never IMAP.
  (b) ~/rick-vault/signals/warm-signals.jsonl for unresponded entries

For each genuine reply (not automated/notification), classifies intent:
  CALL-intent  — mentions call / interested / pricing / let's talk
  ROAST-intent — mentions roast / audit / feedback
  other

CALL-intent leads are appended to ~/rick-vault/control/call-queue.jsonl and a
'reply' event is logged to the attribution ledger.

2026-07-16 cancel-reason capture: an email reply from a canceling/canceled
customer (customers table) is the answer to a churn-save "why did you cancel"
ask. Those answers used to evaporate in the triage inbox — now they are tagged
churn_feedback and stored durably in BOTH ~/rick-vault/churn/cancel-reasons.jsonl
and a customer_events row (event_type=cancel_reason) before any routing.

Triage + queue only — does NOT auto-send any replies. Caps at 50 items.

Usage:
  python3 scripts/reply-triage.py
"""
import os
import re
import sys
import json
import uuid
import subprocess
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))  # workspace root, for runtime.db
import attribution  # noqa: E402

VAULT = os.path.expanduser("~/rick-vault")
WARM_SIGNALS = os.path.join(VAULT, "signals", "warm-signals.jsonl")
CALL_QUEUE = os.path.join(VAULT, "control", "call-queue.jsonl")
STATE_FILE = os.path.join(VAULT, "control", "reply-triage-state.json")
TRIAGE_DIR = os.path.join(VAULT, "mailbox", "triage")
CANCEL_REASONS = os.path.join(VAULT, "churn", "cancel-reasons.jsonl")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

PROCESS_CAP = 50

CALL_TERMS = ["call", "interested", "pricing", "price", "how much", "cost",
              "let's talk", "lets talk", "schedule", "book", "demo", "sign up",
              "sign me up", "yes", "sounds good", "let's do"]
ROAST_TERMS = ["roast", "audit", "feedback", "review my", "findings"]
AUTO_MARKERS = ["no-reply", "noreply", "donotreply", "do-not-reply",
                "mailer-daemon", "postmaster", "notification", "unsubscribe",
                "automated", "out of office", "auto-reply", "delivery status"]
# Platform-notification senders (2026-07-16): bulk product mail that is never
# a human reply (a facebookmail reminder hit the call-queue on Jul 15).
# Matched against the sender only, so a lead mentioning e.g. "linkedin.com"
# in a subject still gets classified.
PLATFORM_SENDERS = ["facebookmail.com", "linkedin.com", "producthunt.com",
                    "notifications.resend.com", "instagram.com"]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"processed": []}
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
            d.setdefault("processed", [])
            return d
    except Exception:
        return {"processed": []}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        sys.stderr.write("save_state WARN: %s\n" % (str(e)[:160]))


def is_automated(sender, subject):
    sender_l = (sender or "").lower()
    if any(d in sender_l for d in PLATFORM_SENDERS):
        return True
    blob = (sender_l + " " + (subject or "").lower())
    return any(m in blob for m in AUTO_MARKERS)


def classify(text):
    t = (text or "").lower()
    if any(term in t for term in CALL_TERMS):
        return "CALL"
    if any(term in t for term in ROAST_TERMS):
        return "ROAST"
    return "other"


def fetch_triage():
    """Return list of {id, sender, subject, text} from imap-watcher triage JSONL.

    2026-07-13 single-consumer inbox: this used to run
    `himalaya envelope list 'not flag seen'` — a second IMAP consumer racing
    imap-watcher on the destructive \\Seen flag (and losing: imap-watcher marks
    everything Seen within 10 min, so this saw nothing since Jun 11). Now it
    reads the triage JSONL imap-watcher writes. State-file dedupe keeps it
    idempotent across runs.
    """
    items = []
    for offset in (0, 1):
        day = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(days=offset)).strftime("%Y-%m-%d")
        path = os.path.join(TRIAGE_DIR, "inbound-%s.jsonl" % day)
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # Skip rows the Phase G classifier already tagged as
                    # bot/platform mail — keeps noise out of the call-queue.
                    if row.get("classification") == "automated_notification":
                        continue
                    sender = row.get("from") or ""
                    items.append({
                        "id": "triage:" + str(row.get("id") or sender),
                        "sender": sender,
                        "subject": row.get("subject") or "",
                        "text": (row.get("body") or "")[:2000],
                        "channel": "email",
                    })
        except Exception as e:
            sys.stderr.write("reply-triage: triage read error: %s\n" % (str(e)[:160]))
    return items


def fetch_warm_signals():
    items = []
    if not os.path.exists(WARM_SIGNALS):
        return items
    try:
        with open(WARM_SIGNALS) as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("responded") or obj.get("status") == "responded":
                    continue
                sender = obj.get("from") or obj.get("lead") or obj.get("handle") or obj.get("url") or ""
                text = obj.get("text") or obj.get("signal") or obj.get("message") or ""
                items.append({
                    "id": "warm:" + str(obj.get("id") or obj.get("ts") or (str(i) + ":" + sender)),
                    "sender": sender,
                    "subject": "",
                    "text": text,
                    "channel": obj.get("channel") or "warm-signal",
                })
    except Exception as e:
        sys.stderr.write("reply-triage: warm-signals error: %s\n" % (str(e)[:160]))
    return items


def queue_call(item, intent):
    rec = {
        "ts": _now(),
        "lead": item.get("sender"),
        "channel": item.get("channel"),
        "intent": intent,
        "subject": item.get("subject"),
        "src_id": item.get("id"),
        "status": "queued",
    }
    try:
        os.makedirs(os.path.dirname(CALL_QUEUE), exist_ok=True)
        with open(CALL_QUEUE, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        sys.stderr.write("queue_call WARN: %s\n" % (str(e)[:160]))


def _sender_email(sender):
    m = EMAIL_RE.search(sender or "")
    return m.group(0).lower() if m else ""


def load_churn_customers():
    """Map email(lower) -> {id, status} for canceling/canceled customers.

    Fail-loud-but-degrade: a DB hiccup must not kill the call-queue path, so
    on error we warn to stderr (heartbeat keeps stderr) and return {}. Known
    gap: with {} this batch's churn replies route as ordinary replies and ARE
    marked processed — their cancel reasons are NOT retried later (the raw
    reply survives in mailbox/triage/inbound-*.jsonl for manual recovery).
    """
    try:
        from runtime.db import connect  # noqa: E402
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT id, email, status FROM customers "
                "WHERE status IN ('canceling', 'canceled')"
            ).fetchall()
            return {str(r["email"]).lower(): {"id": r["id"], "status": r["status"]}
                    for r in rows}
        finally:
            conn.close()
    except Exception as e:
        sys.stderr.write(
            "reply-triage: churn-customer lookup FAILED (cancel reasons NOT "
            "captured this run): %s\n" % (str(e)[:200]))
        return {}


def record_cancel_reason(item, email, cust):
    """Durably store a churn-save reply in cancel-reasons.jsonl + customer_events.

    Returns True only if BOTH stores took the record; on False the caller must
    leave the item unprocessed so the next run retries instead of evaporating
    the one datum the churn saves exist to collect.
    """
    rec = {
        "ts": _now(),
        "customer": email,
        "customer_id": cust["id"],
        "customer_status": cust["status"],
        "source": "reply-triage",
        "email_id": (item.get("id") or "").split(":", 1)[-1],
        "subject": item.get("subject") or "",
        "verbatim_text": (item.get("text") or "").strip(),
        "tag": "churn_feedback",
    }
    ok = True
    try:
        os.makedirs(os.path.dirname(CANCEL_REASONS), exist_ok=True)
        with open(CANCEL_REASONS, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        ok = False
        sys.stderr.write("record_cancel_reason jsonl FAILED: %s\n" % (str(e)[:200]))
    try:
        from runtime.db import connect  # noqa: E402
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO customer_events (id, customer_id, workflow_id, "
                "event_type, payload_json, created_at) "
                "VALUES (?, ?, NULL, 'cancel_reason', ?, ?)",
                ("evt_%s" % uuid.uuid4().hex[:12], cust["id"],
                 json.dumps(rec, ensure_ascii=False), rec["ts"]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        ok = False
        sys.stderr.write("record_cancel_reason customer_events FAILED: %s\n" % (str(e)[:200]))
    return ok


def main():
    state = load_state()
    processed = set(state.get("processed", []))
    churn_customers = load_churn_customers()

    items = fetch_triage() + fetch_warm_signals()
    items = items[:PROCESS_CAP]

    replies_found = 0
    call_intent = 0
    routed = 0
    churn_feedback = 0
    new_processed = []

    for it in items:
        iid = it.get("id")
        if iid in processed:
            continue
        if is_automated(it.get("sender"), it.get("subject")):
            new_processed.append(iid)
            continue

        replies_found += 1
        text_for_class = (it.get("subject") or "") + " " + (it.get("text") or "")
        intent = classify(text_for_class)

        # Cancel-reason capture: churn-save answers get stored durably BEFORE
        # any routing. On store failure, skip marking processed so the next
        # run retries — the reply must not evaporate.
        churn_tag = ""
        if it.get("channel") == "email":
            cust = churn_customers.get(_sender_email(it.get("sender")))
            if cust:
                if not record_cancel_reason(it, _sender_email(it.get("sender")), cust):
                    continue
                churn_feedback += 1
                churn_tag = ";churn_feedback"

        attribution.log_event(
            stage="reply",
            channel=it.get("channel"),
            asset_id=None,
            src="reply-triage",
            lead=it.get("sender"),
            detail="intent=%s%s" % (intent, churn_tag),
            amount=0,
        )

        if intent == "CALL":
            call_intent += 1
            queue_call(it, intent)
            routed += 1

        new_processed.append(iid)

    if new_processed:
        state["processed"] = list(processed) + new_processed
        # keep last 2000 ids to bound the state file
        state["processed"] = state["processed"][-2000:]
        save_state(state)

    print("reply-triage: %d replies found, %d call-intent, %d routed to call-queue, "
          "%d churn-feedback captured" % (replies_found, call_intent, routed, churn_feedback))
    return 0


if __name__ == "__main__":
    sys.exit(main())
