#!/usr/bin/env python3
"""
day14-gate.py — Day-14 kill-gate scoreboard (strategy 2026-07-13).

Regenerates ~/rick-vault/control/day14-gate.md from ledgers that already
exist, so the 2026-07-27 kill/keep call is a data decision. Invoked from
scripts/run-heartbeat.sh on every heartbeat — same no-new-jobs pattern as
the approvals mirror (rick-exec.py regenerate_approvals_mirror). The
generated .md is the ONLY thing this script writes; every other source is
opened read-only (runtime DB via sqlite mode=ro).

Kill criterion, verbatim from decisions/strategy-2026-07-13.md:
  "Day 14 (2026-07-27) leading gate: 60-80 concierge touches produce ZERO
   booked calls AND the LinguaLive channel push produces ~0 incremental
   signups above baseline -> both cold, escalate."

Sources (all under ~/rick-vault unless noted):
  - go-to-market/concierge-batch-2026-07-14/CHECKLIST.md  "Send tracking"
    checkboxes + concierge-batch-2026-07-14/sent/*.md     (concierge touches;
    Vlad hand-sends leave no trace in Rick's ledgers by design, so the
    checklist checkbox convention is the ledger — both patterns counted)
  - operations/email-sends.jsonl                          (Rick outbound)
  - mailbox/sent/*.json + mailbox/outbox/founder-*.json   (recipient->category map)
  - mailbox/triage/inbound-*.jsonl                        (human replies; reuses
    reply-triage.py's platform-noise exclusions)
  - control/call-queue.jsonl                              (CALL-intent, drills excluded)
  - operations/pilot-intake.jsonl + pilot-lead-poll-state.json
    + runtime DB prospect_pipeline platform='pilot-form'  (pilot leads)
  - runtime DB customers table                            (LinguaLive subs / churn saves)

Both-interpreter safe: runs on system /usr/bin/python3 (3.9) and homebrew
python3 (3.12+). Stdlib only, no LLM, deterministic.

Usage: python3 scripts/day14-gate.py [--stdout]
"""
import glob
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timezone

VAULT = os.path.expanduser("~/rick-vault")
OUT_FILE = os.path.join(VAULT, "control", "day14-gate.md")
CHECKLIST = os.path.join(
    VAULT, "go-to-market", "concierge-batch-2026-07-14", "CHECKLIST.md")
CONCIERGE_SENT_DIR = os.path.join(
    VAULT, "go-to-market", "concierge-batch-2026-07-14", "sent")
EMAIL_SENDS = os.path.join(VAULT, "operations", "email-sends.jsonl")
MAILBOX_SENT = os.path.join(VAULT, "mailbox", "sent")
MAILBOX_OUTBOX = os.path.join(VAULT, "mailbox", "outbox")
TRIAGE_DIR = os.path.join(VAULT, "mailbox", "triage")
CALL_QUEUE = os.path.join(VAULT, "control", "call-queue.jsonl")
LINGUALIVE_ARM = os.path.join(VAULT, "control", "lingualive-arm.json")
PILOT_INTAKE = os.path.join(VAULT, "operations", "pilot-intake.jsonl")
PILOT_POLL_STATE = os.path.join(VAULT, "operations", "pilot-lead-poll-state.json")
RUNTIME_DB = os.environ.get(
    "RICK_RUNTIME_DB_FILE", os.path.join(VAULT, "runtime", "rick-runtime.db"))

WINDOW_START = "2026-07-13"          # strategy date; all "since" counting starts here
GATE_DATE = date(2026, 7, 27)        # Day-14 leading gate
TOUCH_TARGET_LOW, TOUCH_TARGET_HIGH = 60, 80

DAY14_RULE = (
    'Day 14 (2026-07-27) leading gate: 60-80 concierge touches produce ZERO '
    'booked calls AND the LinguaLive channel push produces ~0 incremental '
    'signups above baseline -> both cold, escalate.')
DAY21_PIVOT = (
    'If BOTH fail -> the apparatus has no paying market as built; redirect to '
    "whichever single product can actually grow (likely a new consumer app in "
    "LinguaLive's mold).")

# Noise exclusions — keep in sync with scripts/reply-triage.py (2026-07-16).
PLATFORM_SENDERS = ["facebookmail.com", "linkedin.com", "producthunt.com",
                    "notifications.resend.com", "instagram.com"]
AUTO_MARKERS = ["no-reply", "noreply", "donotreply", "do-not-reply",
                "mailer-daemon", "postmaster", "notification", "unsubscribe",
                "automated", "out of office", "auto-reply", "delivery status"]


def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def looks_like_drill(sender, subject="", src=""):
    s = (sender or "").lower()
    return ("@example." in s
            or "[drill]" in (subject or "").lower()
            or "drill" in (src or "").lower())


# ── 1. Concierge touches (Vlad hand-sends, checklist convention) ─────────────

def concierge_counts():
    """Count Vlad hand-sends. Convention (documented in CHECKLIST.md header):
    tick a draft's '- [ ]' to '- [x]' in the Send tracking section when sent.
    Defensively also counts draft .md files moved into a sent/ subdir."""
    checked, unchecked = set(), set()
    if os.path.exists(CHECKLIST):
        with open(CHECKLIST, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^\s*[-*]\s*\[([ xX])\]\s*(\S+)", line)
                if not m:
                    continue
                key = m.group(2).strip("`")
                if m.group(1).lower() == "x":
                    checked.add(key)
                else:
                    unchecked.add(key)
    moved = set()
    if os.path.isdir(CONCIERGE_SENT_DIR):
        moved = {os.path.basename(p)
                 for p in glob.glob(os.path.join(CONCIERGE_SENT_DIR, "*.md"))}
    sent = checked | moved
    total = len(checked | unchecked | moved)
    return {
        "sent": len(sent),
        "total_tracked": total,
        "via_checkbox": len(checked),
        "via_sent_dir": len(moved),
        "checklist_exists": os.path.exists(CHECKLIST),
        "tracking_section": bool(checked or unchecked),
    }


# ── 2. Rick outbound (email-sends.jsonl) ─────────────────────────────────────

def recipient_category_map():
    """Map recipient -> category from mailbox/sent + outbox payload files.
    Needed because most email-sends.jsonl rows carry no source label."""
    cat_by_to = {}
    precedence = {"churn_save": 3, "warm_revival": 2, "cold_founder": 1, "other": 0}

    def add(to, cat):
        to = (to or "").lower()
        if not to:
            return
        if precedence.get(cat, 0) >= precedence.get(cat_by_to.get(to, "other"), 0):
            cat_by_to[to] = cat

    patterns = ((os.path.join(MAILBOX_SENT, "*.json"), True),
                (os.path.join(MAILBOX_OUTBOX, "founder-*.json"), False))
    for pattern, _ in patterns:
        for path in glob.glob(pattern):
            name = os.path.basename(path).lower()
            if "churn-save" in name or "payment-retry" in name:
                cat = "churn_save"
            elif "warm-revival" in name:
                cat = "warm_revival"
            elif name.startswith("founder-"):
                cat = "cold_founder"
            else:
                cat = "other"
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                add(payload.get("to"), cat)
            except (json.JSONDecodeError, OSError):
                continue
    return cat_by_to


def categorize_send(row, cat_by_to):
    source = " ".join(str(row.get(k) or "") for k in ("source", "path")).lower()
    if "churn-save" in source or "churn_save" in source or "payment-retry" in source:
        return "churn_save"
    if "warm-revival" in source or "warm_revival" in source:
        return "warm_revival"
    if "founder" in source:
        return "cold_founder"
    if ("fulfillment" in source or "delivery" in source or "drain" in source
            or "post-purchase" in source):
        return "other"
    return cat_by_to.get((row.get("to") or "").lower(), "other")


def outbound_counts():
    cat_by_to = recipient_category_map()
    rows = read_jsonl(EMAIL_SENDS)
    # Dedupe on (recipient, UTC day, category): the 2026-07-14 approval drain
    # double-logged the same sends at 21:41Z and 21:43Z with fresh message_ids.
    touches = {}   # category -> set of (to, day)
    people = {}    # category -> set of to
    raw = 0
    for row in rows:
        ts = row.get("ts") or ""
        if row.get("status") != "sent" or ts[:10] < WINDOW_START:
            continue
        # Newsletter broadcast/welcome rows (typed 2026-07-17) are subscriber
        # mail, not outreach touches — a 134-recipient issue must not read as
        # a cold-founder surge here.
        if row.get("type") in ("newsletter", "newsletter_welcome"):
            continue
        raw += 1
        cat = categorize_send(row, cat_by_to)
        to = (row.get("to") or "").lower()
        touches.setdefault(cat, set()).add((to, ts[:10]))
        people.setdefault(cat, set()).add(to)
    queued_founder = 0
    for path in glob.glob(os.path.join(MAILBOX_OUTBOX, "founder-*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                if json.load(f).get("status") == "pending":
                    queued_founder += 1
        except (json.JSONDecodeError, OSError):
            continue
    return {
        "raw_rows": raw,
        "ledger_exists": os.path.exists(EMAIL_SENDS),
        "queued_founder": queued_founder,
        "counts": {cat: {"touches": len(t), "people": len(people.get(cat, set()))}
                   for cat, t in touches.items()},
    }


# ── 3. Human replies (mailbox/triage, reply-triage noise rules) ──────────────

def reply_counts():
    human, noise = [], 0
    files = sorted(glob.glob(os.path.join(TRIAGE_DIR, "inbound-*.jsonl")))
    seen_files = 0
    for path in files:
        day = os.path.basename(path)[len("inbound-"):-len(".jsonl")]
        if day < WINDOW_START:
            continue
        seen_files += 1
        for row in read_jsonl(path):
            ts = (row.get("ingested_at") or day)[:10]
            if ts < WINDOW_START:
                continue
            sender = (row.get("from") or "").lower()
            subject = row.get("subject") or ""
            if (row.get("classification") == "automated_notification"
                    or any(d in sender for d in PLATFORM_SENDERS)
                    or any(m in sender + " " + subject.lower() for m in AUTO_MARKERS)
                    or looks_like_drill(sender, subject)):
                noise += 1
                continue
            human.append((ts, sender, subject[:60]))
    return {"human": human, "noise": noise, "files": seen_files}


# ── 4. Calls booked (control/call-queue.jsonl) ───────────────────────────────

def call_counts():
    real, drills = [], 0
    for row in read_jsonl(CALL_QUEUE):
        if row.get("intent") != "CALL" or (row.get("ts") or "")[:10] < WINDOW_START:
            continue
        if looks_like_drill(row.get("lead"), row.get("subject"), row.get("src_id")):
            drills += 1
            continue
        real.append((row.get("ts", "")[:10], row.get("lead"), row.get("status")))
    return {"real": real, "drills": drills, "exists": os.path.exists(CALL_QUEUE)}


# ── 5. Pilot leads ───────────────────────────────────────────────────────────

def db_connect_ro():
    if not os.path.exists(RUNTIME_DB):
        return None
    try:
        return sqlite3.connect("file:%s?mode=ro" % RUNTIME_DB, uri=True)
    except sqlite3.Error:
        return None


def pilot_counts():
    intake_real = 0
    for row in read_jsonl(PILOT_INTAKE):
        blob = json.dumps(row).lower()
        if row.get("_test") or "test" in str(row.get("source", "")).lower() \
                or "dry_run" in blob or "@example." in blob:
            continue
        ts = (row.get("ts") or "")[:10]
        if ts >= WINDOW_START:
            intake_real += 1
    cursor = {}
    if os.path.exists(PILOT_POLL_STATE):
        try:
            with open(PILOT_POLL_STATE, encoding="utf-8") as f:
                cursor = json.load(f)
        except (json.JSONDecodeError, OSError):
            cursor = {}
    db_rows, db_ok = 0, False
    conn = db_connect_ro()
    if conn is not None:
        try:
            db_rows = conn.execute(
                "SELECT COUNT(*) FROM prospect_pipeline WHERE platform='pilot-form' "
                "AND status NOT LIKE 'drill%' AND username NOT LIKE '%@example.%' "
                "AND created_at >= ?", (WINDOW_START,)).fetchone()[0]
            db_ok = True
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return {"intake_real": intake_real, "db_rows": db_rows, "db_ok": db_ok,
            "cursor": cursor}


# ── 6. LinguaLive (customers table) ──────────────────────────────────────────

def is_meetrick(tags_json, metadata_json):
    return "rick-pro" in (tags_json or "") or "Rick Pro" in (metadata_json or "")


def lingualive_arm_status():
    """Treatment-arm record (briefing 2026-07-18 item 7). None = never armed;
    {"_unreadable": True} = file exists but is corrupt (surface loud)."""
    if not os.path.exists(LINGUALIVE_ARM):
        return None
    try:
        with open(LINGUALIVE_ARM, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"_unreadable": True}


def lingualive_counts():
    out = {"db_ok": False, "new_active": [], "baseline_30d": 0,
           "churn_watch": [], "active_total": 0}
    conn = db_connect_ro()
    if conn is None:
        return out
    try:
        rows = conn.execute(
            "SELECT email, name, status, created_at, tags_json, metadata_json "
            "FROM customers").fetchall()
    except sqlite3.Error:
        conn.close()
        return out
    conn.close()
    out["db_ok"] = True
    churn_recipients = set()
    cat_by_to = recipient_category_map()
    for to, cat in cat_by_to.items():
        if cat == "churn_save":
            churn_recipients.add(to)
    for email, name, status, created_at, tags_json, metadata_json in rows:
        if is_meetrick(tags_json, metadata_json):
            continue
        created = (created_at or "")[:10]
        if status == "active":
            out["active_total"] += 1
        if status == "active" and created >= WINDOW_START:
            try:
                meta = json.loads(metadata_json or "{}")
            except json.JSONDecodeError:
                meta = {}
            out["new_active"].append(
                (email, created, meta.get("amount_usd", "?")))
        if "2026-06-13" <= created < WINDOW_START:
            out["baseline_30d"] += 1
        if email.lower() in churn_recipients:
            try:
                meta = json.loads(metadata_json or "{}")
            except json.JSONDecodeError:
                meta = {}
            out["churn_watch"].append(
                (email, name or "", status, meta.get("current_period_end", "?")))
    out["churn_saved"] = sum(1 for c in out["churn_watch"] if c[2] == "active")
    out["churn_pending"] = sum(1 for c in out["churn_watch"] if c[2] == "canceling")
    return out


# ── Verdict + render ─────────────────────────────────────────────────────────

def render():
    now_local = datetime.now()
    today = date.today()
    days_left = (GATE_DATE - today).days

    con = concierge_counts()
    out = outbound_counts()
    rep = reply_counts()
    calls = call_counts()
    pilots = pilot_counts()
    ll = lingualive_counts()

    calls_n = len(calls["real"])
    pilots_n = pilots["intake_real"] + pilots["db_rows"]
    new_subs = len(ll["new_active"])

    # Engine 1 — meetrick concierge. COLD only counts once the touch
    # precondition (>=60 hand-sends) was actually met; a 0-touch "COLD" would
    # pivot on a test that never ran.
    if calls_n > 0:
        eng1 = "GREEN"
        eng1_note = "%d booked call(s) in the queue" % calls_n
    elif con["sent"] >= TOUCH_TARGET_LOW:
        eng1 = "COLD"
        eng1_note = "%d touches, zero booked calls" % con["sent"]
    else:
        eng1 = "NOT-YET-TESTABLE"
        eng1_note = ("only %d/%d touches sent — the gate test is NOT RUNNING; "
                     "at this pace 2026-07-27 arrives with no data"
                     % (con["sent"], TOUCH_TARGET_LOW))

    # Engine 2 — LinguaLive push: "~0 incremental signups above baseline".
    if not ll["db_ok"]:
        eng2, eng2_note = "UNKNOWN", "runtime DB unavailable — fix before the gate"
    elif new_subs > 0:
        eng2 = "GREEN"
        eng2_note = ("%d new active sub(s) since %s vs baseline %d in the prior "
                     "30 days" % (new_subs, WINDOW_START, ll["baseline_30d"]))
    else:
        eng2 = "COLD"
        eng2_note = "0 new active subs since %s" % WINDOW_START

    both_cold = eng1 == "COLD" and eng2 == "COLD"

    def bullet_counts(cat, label):
        c = out["counts"].get(cat, {"touches": 0, "people": 0})
        return "- **%s:** %d send(s) to %d recipient(s)" % (
            label, c["touches"], c["people"])

    lines = []
    lines.append("# Day-14 Kill Gate — decision 2026-07-27")
    lines.append("")
    lines.append("> GENERATED by `scripts/day14-gate.py` (workspace repo), invoked from")
    lines.append("> `scripts/run-heartbeat.sh` on every heartbeat — same no-new-jobs pattern")
    lines.append("> as the approvals mirror. Do not hand-edit; changes are overwritten.")
    lines.append("> Last generated: %s. Counting window starts %s (strategy date)."
                 % (now_local.strftime("%Y-%m-%d %H:%M:%S"), WINDOW_START))
    lines.append("")
    lines.append("**The rule (verbatim, `decisions/strategy-2026-07-13.md`):** %s" % DAY14_RULE)
    lines.append("")
    lines.append("## Verdict — %d day(s) to the gate" % days_left)
    lines.append("")
    lines.append("| Engine | Criterion | Now | Status |")
    lines.append("|--------|-----------|-----|--------|")
    lines.append("| meetrick concierge | %d-%d hand touches -> ZERO booked calls = cold "
                 "| %d/%d touches, %d call(s), %d pilot(s) | **%s** |"
                 % (TOUCH_TARGET_LOW, TOUCH_TARGET_HIGH, con["sent"],
                    TOUCH_TARGET_LOW, calls_n, pilots_n, eng1))
    lines.append("| LinguaLive push | ~0 incremental signups above baseline = cold "
                 "| %d new active sub(s) since %s (baseline %d/30d) | **%s** |"
                 % (new_subs, WINDOW_START, ll["baseline_30d"], eng2))
    lines.append("")
    lines.append("- meetrick concierge: %s." % eng1_note)
    lines.append("- LinguaLive: %s." % eng2_note)
    if both_cold:
        lines.append("")
        lines.append("**BOTH ENGINES COLD -> escalate (Day-14 rule). The strategy's own "
                     "Day-21 sentence: \"%s\"**" % DAY21_PIVOT)
    else:
        lines.append("- Gate verdict today: both-cold pivot NOT triggered "
                     "(%s / %s)." % (eng1, eng2))
    lines.append("")

    lines.append("## 1. Concierge touches (Vlad hand-sends) — %d sent" % con["sent"])
    lines.append("")
    if not con["checklist_exists"]:
        lines.append("- **CHECKLIST.md MISSING** — cannot count hand-sends.")
    elif not con["tracking_section"]:
        lines.append("- **No Send-tracking checkboxes found in CHECKLIST.md** — "
                     "convention broken, count unreliable.")
    else:
        lines.append("- %d ticked `[x]` in CHECKLIST.md Send tracking, %d draft(s) in "
                     "`sent/` subdir (union counted once); %d draft(s) tracked total."
                     % (con["via_checkbox"], con["via_sent_dir"], con["total_tracked"]))
    lines.append("- These are sent from Vlad's own mailbox/accounts by design and leave "
                 "no trace in Rick's ledgers — the checklist tick IS the ledger.")
    lines.append("- Gate needs %d-%d touches; batch of 20 covers the first quarter only."
                 % (TOUCH_TARGET_LOW, TOUCH_TARGET_HIGH))
    lines.append("")

    lines.append("## 2. Rick outbound since %s (context — NOT the concierge test)" % WINDOW_START)
    lines.append("")
    lines.append(bullet_counts("cold_founder", "Cold founder sends (Show-HN sourcer)"))
    if out["queued_founder"]:
        lines.append("  - plus %d founder draft(s) queued in outbox (veto window, not sent)"
                     % out["queued_founder"])
    lines.append(bullet_counts("warm_revival", "Warm revivals"))
    lines.append(bullet_counts("churn_save", "Churn saves (incl. payment-retry)"))
    lines.append(bullet_counts("other", "Other sends (fulfillment/delivery)"))
    lines.append("- %d raw ledger rows in window; duplicates collapsed on "
                 "(recipient, day) — the 07-14 approval drain double-logged sends."
                 % out["raw_rows"])
    if not out["ledger_exists"]:
        lines.append("- **email-sends.jsonl MISSING** — outbound counts unreliable.")
    lines.append("")

    lines.append("## 3. Replies — %d human inbound" % len(rep["human"]))
    lines.append("")
    if rep["files"] == 0:
        lines.append("- **No triage files since %s** — imap-watcher may be down; "
                     "reply count unreliable." % WINDOW_START)
    else:
        lines.append("- %d noise row(s) excluded (platform senders / auto markers / "
                     "`automated_notification` / drills) across %d triage file(s) — "
                     "same exclusion list as reply-triage.py." % (rep["noise"], rep["files"]))
    for ts, sender, subject in rep["human"][:10]:
        lines.append("- %s — %s — %s" % (ts, sender, subject))
    lines.append("")

    lines.append("## 4. Calls booked — %d" % calls_n)
    lines.append("")
    lines.append("- **cal.com is NOT live** — no booking link exists; counting CALL-intent "
                 "entries in `control/call-queue.jsonl` as the closest proxy until it is.")
    lines.append("- %d drill/test entr(ies) excluded." % calls["drills"])
    for ts, lead, status in calls["real"][:10]:
        lines.append("- %s — %s (%s)" % (ts, lead, status))
    lines.append("")

    lines.append("## 5. Pilot leads — %d" % pilots_n)
    lines.append("")
    lines.append("- pilot-intake.jsonl real entries since %s: %d (test/drill rows excluded)."
                 % (WINDOW_START, pilots["intake_real"]))
    lines.append("- prospect_pipeline `pilot-form` rows since %s: %s."
                 % (WINDOW_START,
                    str(pilots["db_rows"]) if pilots["db_ok"] else "**DB UNAVAILABLE**"))
    lines.append("- Poll cursor: last_id `%s`, updated %s — unchanged cursor + "
                 "`run.no_leads` means zero API-side leads."
                 % (pilots["cursor"].get("last_id", "?"),
                    pilots["cursor"].get("updated_at", "?")))
    lines.append("")

    lines.append("## 6. LinguaLive — %d new active sub(s) since %s"
                 % (new_subs, WINDOW_START))
    lines.append("")
    # Treatment arm (strategy §4 / briefing 2026-07-18 item 7): both the
    # Day-14 and Day-21(b) readings of this doc must show whether the
    # UTM experiment actually ran, or "~0 incremental" is unjudgeable.
    arm = lingualive_arm_status()
    if arm is None:
        lines.append("- Treatment arm (strategy §4): **NOT ARMED** — no "
                     "`control/lingualive-arm.json`; Day-21(b) has no "
                     "treatment data.")
    elif arm.get("_unreadable"):
        lines.append("- Treatment arm (strategy §4): **ARM FILE UNREADABLE** — "
                     "fix `control/lingualive-arm.json` before the gate.")
    else:
        armed_date = (arm.get("armed_at") or "?")[:10]
        if arm.get("sent_at"):
            arm_state = "sent %s in issue %s" % (
                str(arm.get("sent_at"))[:10], arm.get("sent_issue") or "?")
        else:
            arm_state = "staged, NOT yet sent"
        lines.append("- Treatment arm (strategy §4): **ARMED %s** via %s, "
                     "campaign `%s` — %s; baseline %s active sub(s) at arming."
                     % (armed_date, arm.get("channel", "?"),
                        arm.get("utm_campaign", "?"), arm_state,
                        arm.get("baseline_active_subs", "?")))
    if not ll["db_ok"]:
        lines.append("- **runtime DB unavailable — LinguaLive numbers missing.**")
    else:
        for email, created, amount in sorted(ll["new_active"], key=lambda r: r[1]):
            lines.append("- new: %s (created %s, $%s/mo)" % (email, created, amount))
        lines.append("- Baseline: %d sub(s) created in the prior 30 days (%s..%s); "
                     "created_at = first-seen (webhook was down until the 07-13/14 "
                     "backfill), so early-window rows may lag the real purchase date."
                     % (ll["baseline_30d"], "2026-06-13", WINDOW_START))
        lines.append("- Churn saves: %d recovered, %d still canceling of %d targeted:"
                     % (ll["churn_saved"], ll["churn_pending"], len(ll["churn_watch"])))
        for email, name, status, period_end in sorted(ll["churn_watch"],
                                                      key=lambda r: str(r[3])):
            label = ("SAVED (active)" if status == "active"
                     else "%s, lapses %s" % (status, period_end))
            lines.append("  - %s%s — %s" % (email, " (%s)" % name if name else "", label))
        lines.append("- Net: %d active non-meetrick sub(s) right now (meetrick rick-pro "
                     "excluded; portfolio is never summed with Rick's own MRR)."
                     % ll["active_total"])
    lines.append("")

    lines.append("## Sources")
    lines.append("")
    lines.append("- `go-to-market/concierge-batch-2026-07-14/CHECKLIST.md` (+ optional `sent/` subdir)")
    lines.append("- `operations/email-sends.jsonl` · `mailbox/sent/*.json` · `mailbox/outbox/founder-*.json`")
    lines.append("- `mailbox/triage/inbound-*.jsonl` · `control/call-queue.jsonl`")
    lines.append("- `control/lingualive-arm.json` (treatment-arm record, briefing 2026-07-18 item 7)")
    lines.append("- `operations/pilot-intake.jsonl` · `operations/pilot-lead-poll-state.json`")
    lines.append("- runtime DB (read-only): `customers`, `prospect_pipeline`")
    lines.append("- Rule source: `decisions/strategy-2026-07-13.md` (KILL CRITERIA)")
    lines.append("")
    return "\n".join(lines)


def main():
    body = render()
    if "--stdout" in sys.argv[1:]:
        print(body)
        return 0
    out_dir = os.path.dirname(OUT_FILE)
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
    except BaseException:
        os.unlink(tmp)
        raise
    os.replace(tmp, OUT_FILE)
    print("day14-gate: wrote %s" % OUT_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
