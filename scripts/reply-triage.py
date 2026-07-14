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

Triage + queue only — does NOT auto-send any replies. Caps at 50 items.

Usage:
  python3 scripts/reply-triage.py
"""
import os
import sys
import json
import subprocess
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import attribution  # noqa: E402

VAULT = os.path.expanduser("~/rick-vault")
WARM_SIGNALS = os.path.join(VAULT, "signals", "warm-signals.jsonl")
CALL_QUEUE = os.path.join(VAULT, "control", "call-queue.jsonl")
STATE_FILE = os.path.join(VAULT, "control", "reply-triage-state.json")
TRIAGE_DIR = os.path.join(VAULT, "mailbox", "triage")

PROCESS_CAP = 50

CALL_TERMS = ["call", "interested", "pricing", "price", "how much", "cost",
              "let's talk", "lets talk", "schedule", "book", "demo", "sign up",
              "sign me up", "yes", "sounds good", "let's do"]
ROAST_TERMS = ["roast", "audit", "feedback", "review my", "findings"]
AUTO_MARKERS = ["no-reply", "noreply", "donotreply", "do-not-reply",
                "mailer-daemon", "postmaster", "notification", "unsubscribe",
                "automated", "out of office", "auto-reply", "delivery status"]


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
    blob = ((sender or "") + " " + (subject or "")).lower()
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


def main():
    state = load_state()
    processed = set(state.get("processed", []))

    items = fetch_triage() + fetch_warm_signals()
    items = items[:PROCESS_CAP]

    replies_found = 0
    call_intent = 0
    routed = 0
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

        attribution.log_event(
            stage="reply",
            channel=it.get("channel"),
            asset_id=None,
            src="reply-triage",
            lead=it.get("sender"),
            detail="intent=%s" % intent,
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

    print("reply-triage: %d replies found, %d call-intent, %d routed to call-queue" % (
        replies_found, call_intent, routed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
