#!/usr/bin/env python3
"""
fix-sequence.py — the 5-day roast-lead "Fix Sequence" nurture for Rick's growth machine v2.

Reads pending roast leads from ~/rick-vault/projects/outreach/roast-leads.jsonl.
For each lead, figures out which sequence step is due (by days since captured_at)
and whether it was already sent (state in ~/rick-vault/projects/email/fix-sequence-state.json,
keyed by email). Sends via Resend, validating every address through
runtime/email_validator.validate_for_outbound BEFORE sending.

Sequence (growth plan 5b):
  Day 0  — full roast delivery + #1 fix teaser
  Day 1  — deep-dive on worst finding + how Rick fixes it (soft reply CTA)
  Day 3  — proof: $2,375 case + CALL cta
  Day 5  — direct $2,500/mo offer
  Day 12 — one-line breakup

Usage:
  python3 scripts/fix-sequence.py             # live send (cap 30/run)
  python3 scripts/fix-sequence.py --dry-run   # print what WOULD send, no Resend call
"""
import os
import sys
import json
import datetime
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(WORKSPACE, "runtime"))

import attribution  # noqa: E402

try:
    from email_validator import validate_for_outbound
except Exception:  # tolerate a missing validator — fail closed (skip all)
    validate_for_outbound = None

VAULT = os.path.expanduser("~/rick-vault")
LEADS_FILE = os.path.join(VAULT, "projects", "outreach", "roast-leads.jsonl")
STATE_FILE = os.path.join(VAULT, "projects", "email", "fix-sequence-state.json")
SKIP_LOG = os.path.join(VAULT, "logs", "skipped-addresses.jsonl")
RUNTIME_DB = os.path.join(VAULT, "runtime", "rick-runtime.db")

RESEND_API = "https://api.resend.com/emails"
FROM_ADDR = "Rick <rick@meetrick.ai>"
SEND_CAP = 30

DRY_RUN = "--dry-run" in sys.argv

# Sequence steps: (key, day_threshold)
STEPS = [
    ("day0", 0),
    ("day1", 1),
    ("day3", 3),
    ("day5", 5),
    ("day12", 12),
]


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(ts):
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        return None


def load_leads():
    leads = []
    if not os.path.exists(LEADS_FILE):
        return leads
    try:
        with open(LEADS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("email"):
                    leads.append(obj)
    except Exception as e:
        sys.stderr.write("load_leads WARN: %s\n" % (str(e)[:200]))
    return leads


def load_deal_close_emails():
    """Emails already owned by a deal_close workflow (rick-runtime.db).

    deal_close is the CANONICAL follow-up path for roast leads (WS-C
    2026-07-13) — fix-sequence must never double-enroll an email the
    deal-closer chain is already working. Returns a set of lowercased
    emails, or None when the DB can't be read (caller fails closed).
    """
    try:
        import sqlite3
        conn = sqlite3.connect("file:%s?mode=ro" % RUNTIME_DB, uri=True, timeout=5)
        try:
            rows = conn.execute(
                "SELECT context_json FROM workflows WHERE kind='deal_close'"
            ).fetchall()
        finally:
            conn.close()
        emails = set()
        for (ctx,) in rows:
            try:
                trig = json.loads(ctx).get("trigger_payload", {})
            except (json.JSONDecodeError, AttributeError):
                continue
            em = (trig.get("email") or "").strip().lower()
            if em:
                emails.add(em)
        return emails
    except Exception as e:
        sys.stderr.write("load_deal_close_emails WARN: %s\n" % (str(e)[:200]))
        return None


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        sys.stderr.write("save_state WARN: %s\n" % (str(e)[:200]))


def log_skip(email, reason):
    try:
        os.makedirs(os.path.dirname(SKIP_LOG), exist_ok=True)
        rec = {
            "ts": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "email": email,
            "reason": reason,
            "src": "fix-sequence",
        }
        with open(SKIP_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def due_step(lead, state):
    """Return the step key that is due now for this lead, or None.

    Sends the EARLIEST unsent step whose day threshold has been reached, so a
    lead that just entered never skips Day 0 even if it's older.
    """
    captured = _parse_iso(lead.get("captured_at"))
    if captured is None:
        return None
    days_elapsed = (_now() - captured).total_seconds() / 86400.0
    sent = set(state.get(lead["email"], {}).get("sent", []))
    for key, threshold in STEPS:
        if key in sent:
            continue
        if days_elapsed >= threshold:
            return key
        else:
            # earliest unsent step not yet due → nothing due
            return None
    return None


def biz_name(lead):
    return lead.get("company") or lead.get("business") or lead.get("business_name") or "your site"


def worst_finding(lead):
    rf = lead.get("roast_findings") or lead.get("roast_notes")
    if isinstance(rf, list) and rf:
        return str(rf[0])
    if isinstance(rf, str) and rf.strip():
        return rf.strip()
    return None


def build_email(lead, step):
    """Return (subject, html, text) for the given step. On-brand: sharp, warm,
    direct, no corporate speak."""
    biz = biz_name(lead)
    first = lead.get("first_name") or ""
    hi = ("Hey %s," % first) if first else "Hey,"
    finding = worst_finding(lead)
    finding_line = (
        "The worst one: %s." % finding if finding
        else "The worst one's the kind of thing that quietly leaks leads every single day."
    )

    if step == "day0":
        subject = "Your %s roast (and the #1 thing I'd fix first)" % biz
        body = (
            "%s\n\n"
            "I ran an AI audit on %s. Full roast below.\n\n"
            "%s\n\n"
            "If you fix nothing else this week, fix that one. It's the cheapest "
            "lead you're not capturing.\n\n"
            "I'll send a deeper breakdown of it tomorrow — exactly how I'd fix it "
            "if it were mine.\n\n"
            "— Rick\n"
            "meetrick.ai"
        ) % (hi, biz, finding_line)

    elif step == "day1":
        subject = "How I'd actually fix the worst thing on %s" % biz
        body = (
            "%s\n\n"
            "Yesterday I flagged the #1 issue on %s. Here's the deep dive.\n\n"
            "%s\n\n"
            "When I run this for clients, the fix is boring and it works: tighten "
            "the offer, make the next step obvious, then follow up the leads you're "
            "already getting before they go cold. Most sites don't have a traffic "
            "problem — they have a follow-up problem.\n\n"
            "Want me to map out the exact fix for %s? Just reply.\n\n"
            "— Rick"
        ) % (hi, biz, finding_line, biz)

    elif step == "day3":
        subject = "Receipts, not promises"
        body = (
            "%s\n\n"
            "Quick proof before I stop emailing you about %s.\n\n"
            "I don't just audit — I run the channel, and I publish my receipts "
            "in public: every week, what shipped, what converted, what flopped. "
            "No consultant deck survives that kind of honesty.\n\n"
            "This week's ledger: https://meetrick.ai/this-week?utm_source=fix-sequence&utm_medium=email&utm_campaign=roast_d3\n\n"
            "Want me to walk you through your fix plan on a 15-min call? Reply "
            "'CALL' and I'll send a couple times.\n\n"
            "— Rick"
        ) % (hi, biz)

    elif step == "day5":
        subject = "Want me to just run it for you?"
        weak = finding or "your weakest channel"
        body = (
            "%s\n\n"
            "Straight offer, no fluff.\n\n"
            "Managed pilot: $499/mo. I run %s for %s — content, email, "
            "follow-up, the works — and send receipts weekly. Cancel anytime. "
            "20 pilot seats: https://meetrick.ai/pilot?utm_source=fix-sequence&utm_medium=email&utm_campaign=roast_d5\n\n"
            "Not ready for that? Rick Pro is $29/mo and handles the follow-up "
            "layer on autopilot: https://meetrick.ai/pro?utm_source=fix-sequence&utm_medium=email&utm_campaign=roast_d5\n\n"
            "Reply and I'll get you set up this week. Otherwise I'll assume "
            "you've got it handled and stop nudging.\n\n"
            "— Rick\n"
            "meetrick.ai"
        ) % (hi, weak, biz)

    elif step == "day12":
        subject = "Closing your file"
        body = (
            "%s\n\n"
            "Closing out %s on my end — want me to keep the roast findings on "
            "record in case you circle back?\n\n"
            "One-word reply works.\n\n"
            "— Rick"
        ) % (hi, biz)

    else:
        subject = "Following up"
        body = "%s\n\nFollowing up on your %s roast.\n\n— Rick" % (hi, biz)

    # Working opt-out on every step — replies land in the monitored inbox and
    # 'unsubscribe'/'stop' feed the suppression list via the reply router.
    body += "\n\nP.S. Not useful? Reply 'stop' and I'll close your file — no hard feelings."

    html = "<div>" + body.replace("\n", "<br>\n") + "</div>"
    return subject, html, body


def _recipient_gate(to_addr):
    """Unified fail-closed per-recipient gate (2026-07-13) — same pattern as
    drip-sender.py. This hourly LIVE cron used to POST Resend with no gate."""
    try:
        import sys as _sys
        wsroot = os.path.expanduser("~/.openclaw/workspace")
        if wsroot not in _sys.path:
            _sys.path.insert(0, wsroot)
        from runtime.kill_switches import is_send_allowed
        return is_send_allowed(to_addr, cold=False)
    except Exception as exc:  # gate unavailable => refuse to send
        return False, f"gate_unavailable:{type(exc).__name__}"


def send_resend(to_addr, subject, html, text, api_key):
    allowed, gate_reason = _recipient_gate(to_addr)
    if not allowed:
        print(f"SEND_BLOCKED reason={gate_reason} to={to_addr}")
        return {"blocked": gate_reason}
    payload = {
        "from": FROM_ADDR,
        "to": [to_addr],
        "subject": subject,
        "html": html,
        "text": text,
    }
    req = urllib.request.Request(
        RESEND_API,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "User-Agent": "meetrick-rick/1.0",
        },
    )
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read())


def main():
    api_key = os.environ.get("RESEND_API_KEY", "")
    leads = load_leads()
    state = load_state()

    if not leads:
        print("fix-sequence: 0 leads in %s — nothing to do" % LEADS_FILE)
        return 0

    if not DRY_RUN and not api_key:
        print("fix-sequence: no RESEND_API_KEY in env — cannot send (use --dry-run to preview)")
        return 0

    if validate_for_outbound is None and not DRY_RUN:
        print("fix-sequence: email_validator unavailable — failing closed, sending nothing")
        return 0

    # Dedup vs the canonical deal_close chain. Unreadable DB => we can't
    # prove a lead isn't already being worked, so fail closed for this run
    # (hourly cron self-heals; a skipped hour beats a double sequence).
    deal_close_emails = load_deal_close_emails()
    if deal_close_emails is None and not DRY_RUN:
        print("fix-sequence: cannot read deal_close workflows — failing closed, sending nothing")
        return 0
    deal_close_emails = deal_close_emails or set()

    sent = 0
    skipped = 0
    would_send = []

    for lead in leads:
        if sent >= SEND_CAP:
            break
        email = (lead.get("email") or "").strip()
        if not email:
            continue
        if lead.get("unsubscribed") or lead.get("converted"):
            continue
        if email.lower() in deal_close_emails:
            log_skip(email, "deal_close_canonical")
            skipped += 1
            continue
        step = due_step(lead, state)
        if not step:
            continue

        # Validate address before every send.
        if validate_for_outbound is not None:
            ok, reason = validate_for_outbound(email)
            if not ok:
                log_skip(email, reason)
                skipped += 1
                continue

        subject, html, text = build_email(lead, step)

        if DRY_RUN:
            would_send.append((email, step, subject))
            # Don't mutate state in dry-run.
            sent += 1
            continue

        try:
            res = send_resend(email, subject, html, text, api_key)
            msg_id = res.get("id", "")
        except urllib.error.HTTPError as e:
            sys.stderr.write("send FAIL %s step=%s: HTTP %s %s\n" % (
                email, step, e.code, e.read().decode()[:200]))
            continue
        except Exception as e:
            sys.stderr.write("send FAIL %s step=%s: %s\n" % (email, step, str(e)[:200]))
            continue

        # Record state so we never double-send.
        rec = state.setdefault(email, {"sent": []})
        if step not in rec["sent"]:
            rec["sent"].append(step)
        rec["last_step"] = step
        rec["last_sent_at"] = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(state)

        attribution.log_event(
            stage="capture",
            channel="email",
            asset_id="fix-sequence-%s" % step,
            src="roast-lead",
            lead=email,
            detail="fix-sequence %s sent (msg=%s)" % (step, msg_id),
            amount=0,
        )
        sent += 1

    if DRY_RUN:
        print("fix-sequence DRY-RUN: %d would send, %d skipped (validation/dedup)" % (sent, skipped))
        for email, step, subject in would_send[:30]:
            print("  WOULD SEND -> %-35s %-6s %s" % (email, step, subject))
    else:
        print("fix-sequence: %d sent, %d skipped (validation/dedup), cap=%d" % (sent, skipped, SEND_CAP))
    return 0


if __name__ == "__main__":
    sys.exit(main())
