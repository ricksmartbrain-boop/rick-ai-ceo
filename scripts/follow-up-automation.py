#!/usr/bin/env python3
"""
follow-up-automation.py — Automated follow-up email system for meetrick.ai cold outreach

Reads pipeline.jsonl (the single source of truth for all outreach),
identifies leads who need Day 2 or Day 5 follow-ups, sends personalized
emails via Resend, and logs everything to prevent double-sending.

Works alongside campaign-engine.py but is focused exclusively on the
first two follow-up touches (Day 2 and Day 5) with Rick's voice.

Usage:
  python3 follow-up-automation.py --run           # Send all due follow-ups
  python3 follow-up-automation.py --dry-run       # Preview what would send
  python3 follow-up-automation.py --stats          # Show follow-up pipeline stats
"""

import json
import os
import sys
import datetime
import time
import subprocess
import argparse
from pathlib import Path
from collections import defaultdict

# ─── Config ───────────────────────────────────────────────────────────────────
PIPELINE_LOG = Path.home() / "rick-vault/logs/pipeline.jsonl"
FOLLOWUP_LOG = Path.home() / "rick-vault/logs/followup-automation.jsonl"
ENV_FILE = Path.home() / ".openclaw/workspace/config/rick.env"
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SUPPRESSION_FILE = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault"))) / "mailbox" / "suppression.txt"
FROM = "Rick <rick@meetrick.ai>"
DELAY_BETWEEN_SENDS = 1.5  # seconds
MAX_SENDS_PER_RUN = 20
DAILY_CAP = 40  # follow-up specific daily cap

# Day 2: sent 2-4 days after initial contact
DAY2_MIN = 2
DAY2_MAX = 4
DAY2_STAGE = "followup_day2_sent"

# Day 5: sent 5-8 days after initial contact (only if Day 2 was sent)
DAY5_MIN = 5
DAY5_MAX = 8
DAY5_STAGE = "followup_day5_sent"

# Stages that mean "don't follow up"
SKIP_STAGES = {
    "unsubscribed", "optout", "bounced", "FRAUD_ALERT",
    "replied", "engaged", "accepted_proposal", "proposal_sent",
}

# Emails to never contact
BLOCKED_EMAILS = {
    "user@domain.com", "FULL-WHITE@3x.png", "rick@meetrick.ai",
    "vladislav@belkins.io", "vlad@belkins.io",
    "paul25011991z@gmail.com",
}


def email_channel_block_reason():
    try:
        root = str(WORKSPACE_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from runtime.db import connect
        from runtime.kill_switches import ChannelPaused, assert_channel_active

        conn = connect()
        try:
            assert_channel_active(conn, "email")
            return None
        except ChannelPaused as exc:
            return exc.reason
        finally:
            conn.close()
    except Exception as exc:
        return f"gate_unavailable: {type(exc).__name__}: {exc}"


def is_suppressed(email):
    if not SUPPRESSION_FILE.exists():
        return False
    target = (email or "").strip().lower()
    if not target:
        return True
    try:
        lines = SUPPRESSION_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return True
    for raw in lines:
        suppressed = raw.split("#", 1)[0].strip().lower()
        if suppressed and suppressed == target:
            return True
    return False

# Category-specific pain points for personalization
CATEGORY_PAIN = {
    "dentist": "dental sites lose 60% of visitors before they find the contact form",
    "chiropractor": "chiropractic sites rank well but convert terribly — the gap is always copy",
    "salon": "salon sites look beautiful but give people zero reason to book online",
    "barbershop": "barber clients book by word-of-mouth — your site could capture 3x more",
    "gym": "gym sites lead with equipment photos instead of transformations",
    "restaurant": "restaurant sites that make you hunt for the menu lose tables nightly",
    "veterinarian": "vet sites that don't answer 'taking new patients' above the fold lose anxious pet owners",
    "spa": "spa sites without online booking lose every impulse visitor",
    "yoga studio": "yoga sites that hide class schedules behind a login lose curious visitors",
    "realtor": "realtor sites get Zillow overflow traffic and convert almost none of it",
    "law firm": "law firm sites lead with founding year instead of client outcomes",
    "accountant": "accounting sites that say 'trusted since 1987' don't speak to anyone under 45",
    "photographer": "photography sites with no booking path are portfolios, not businesses",
    "tattoo shop": "tattoo sites that bury the booking form lose impulse clients",
    "cleaning service": "cleaning service sites compete on price because they give nothing else to compare",
    "auto repair": "auto repair sites without reviews above the fold lose to whoever has them",
    "optometrist": "optometry sites that don't mention insurance in first scroll lose comparison shoppers",
    "plastic surgeon": "plastic surgery sites lead with credentials instead of outcomes",
    "massage therapist": "massage sites without instant booking lose to the next tab",
    "florist": "florist sites that don't show same-day delivery lose last-minute buyers",
    "personal trainer": "PT sites without a free consultation CTA leave easy leads on the table",
    "interior designer": "design portfolio sites without a 'start your project' CTA are galleries, not pipelines",
    "pet grooming": "pet grooming sites that don't show availability lose to whoever does",
    "orthodontist": "ortho sites that don't answer 'do you accept my insurance' lose families",
}

DEFAULT_PAIN = "most small business sites look credible but don't actually convert — the gap is in the copy and flow"

# Local-SMB follow-ups were killed as an ICP on 2026-04-21 after poor reply
# quality and deliverability risk. Keep the guard default-on so old cron paths
# cannot revive this segment accidentally.
PAUSED_LOCAL_SMB_CATEGORIES = set(CATEGORY_PAIN)
ALLOW_LOCAL_SMB_FOLLOWUPS_ENV = "RICK_ALLOW_LOCAL_SMB_FOLLOWUPS"


def load_env():
    """Load env vars from rick.env if not already set."""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_pipeline():
    """Load all leads from pipeline.jsonl, deduplicating by email."""
    leads = {}
    if not PIPELINE_LOG.exists():
        print(f"❌ Pipeline log not found: {PIPELINE_LOG}")
        return leads

    for line in PIPELINE_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except:
            continue

        email = d.get("email", "")
        if not email or "@" not in email:
            continue
        if email in BLOCKED_EMAILS:
            continue

        ts = str(d.get("ts") or d.get("timestamp") or "")[:10]
        stage = d.get("stage", "")

        if email not in leads:
            leads[email] = {
                "stages": set(),
                "first_ts": ts,
                "biz": d.get("business_name") or d.get("target") or "",
                "city": d.get("city", ""),
                "category": d.get("category", ""),
                "email": email,
            }
        leads[email]["stages"].add(stage)

        # Track earliest contact date
        if ts and ts > "2020":
            if not leads[email]["first_ts"] or ts < leads[email]["first_ts"]:
                leads[email]["first_ts"] = ts

    return leads


def load_followup_log():
    """Load follow-up log to track what we've already sent."""
    sent = set()
    if FOLLOWUP_LOG.exists():
        for line in FOLLOWUP_LOG.read_text().splitlines():
            try:
                d = json.loads(line)
                key = f"{d['email']}:{d['stage']}"
                sent.add(key)
            except:
                continue
    return sent


def days_since(ts_str):
    """Days since a given ISO date string."""
    try:
        sent = datetime.date.fromisoformat(ts_str)
        return (datetime.date.today() - sent).days
    except:
        return 0


def get_daily_sent_count():
    """Count how many follow-ups were sent today."""
    today = datetime.date.today().isoformat()
    count = 0
    if FOLLOWUP_LOG.exists():
        for line in FOLLOWUP_LOG.read_text().splitlines():
            try:
                d = json.loads(line)
                if d.get("ts", "")[:10] == today and d.get("status") == "sent":
                    count += 1
            except:
                continue
    return count


# ─── Email Templates ──────────────────────────────────────────────────────────

def build_day2_email(lead):
    """Day 2: Short, specific, asks ONE question."""
    biz = lead["biz"].replace("https://", "").replace("http://", "").strip("/") or "your site"
    cat = lead.get("category", "")
    pain = CATEGORY_PAIN.get(cat, DEFAULT_PAIN)

    subject = f"Quick question about {biz}"
    body = f"""Hey,

Quick question — is getting more leads from your website something you're working on right now?

I ask because I ran a free audit on {biz} and spotted a pattern I see a lot: {pain}.

The fix is usually simpler than people expect. 60-second free diagnosis here if you want it:

meetrick.ai/roast

Just a yes/no is helpful — tells me if this is worth your time or not.

— Rick
AI CEO, meetrick.ai

P.S. Reply "stop" any time to opt out."""
    return subject, body


def build_day5_email(lead):
    """Day 5: Last touch, offers something free (roast or mini audit)."""
    biz = lead["biz"].replace("https://", "").replace("http://", "").strip("/") or "your site"
    cat = lead.get("category", "")

    subject = f"Free mini audit for {biz} (last note)"
    body = f"""Hey,

Last note from me, I promise.

I built a free tool that runs a full conversion audit on any website in 60 seconds — headline clarity, CTA placement, trust signals, mobile experience, the works.

I already ran it on {biz}. The results are waiting:

meetrick.ai/roast

No signup. No sales call. No follow-ups after this one.

If it helps, great. If not, no hard feelings — deleting your name from my list after today.

— Rick
AI CEO, meetrick.ai

Reply "stop" to opt out."""
    return subject, body


# ─── Email Sending ────────────────────────────────────────────────────────────

def send_email(to, subject, body):
    """Send email via Resend API."""
    block_reason = email_channel_block_reason()
    if block_reason:
        return False, f"channel_paused: {block_reason}"
    if is_suppressed(to):
        return False, f"suppressed: {to}"
    # Unified fail-closed per-recipient gate (2026-07-13). cold=False —
    # day2/day5 follow-ups are scheduled touches, cap handled by scheduler.
    try:
        root = str(WORKSPACE_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from runtime.kill_switches import is_send_allowed

        allowed, gate_reason = is_send_allowed(to, cold=False)
    except Exception as exc:
        allowed, gate_reason = False, f"gate_unavailable: {type(exc).__name__}: {exc}"
    if not allowed:
        print(f"SEND_BLOCKED reason={gate_reason} to={to}")
        return False, f"SEND_BLOCKED {gate_reason}"
    key = os.environ.get("RESEND_API_KEY", "")
    if not key:
        return False, "RESEND_API_KEY not set"

    payload = json.dumps({
        "from": FROM,
        "to": [to],
        "subject": subject,
        "text": body,
    })

    try:
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST", "https://api.resend.com/emails",
                "-H", "Content-Type: application/json",
                "-H", f"Authorization: Bearer {key}",
                "-d", payload,
            ],
            capture_output=True, text=True, timeout=15
        )
        data = json.loads(result.stdout)
        if data.get("id"):
            return True, data["id"]
        return False, str(data)
    except Exception as e:
        return False, str(e)


def log_followup(lead, stage, status, detail=""):
    """Log follow-up to both followup log and pipeline log."""
    entry = {
        "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": stage,
        "email": lead["email"],
        "business_name": lead["biz"],
        "city": lead["city"],
        "category": lead["category"],
        "status": status,
        "detail": detail,
        "source": "follow-up-automation",
    }

    # Write to dedicated follow-up log
    FOLLOWUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FOLLOWUP_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Also write to pipeline log so campaign-engine sees it
    pipeline_entry = {
        "ts": entry["ts"],
        "stage": stage,
        "email": lead["email"],
        "business_name": lead["biz"],
        "city": lead["city"],
        "category": lead["category"],
    }
    if detail:
        pipeline_entry["resend_id"] = detail
    with open(PIPELINE_LOG, "a") as f:
        f.write(json.dumps(pipeline_entry) + "\n")


# ─── Main Logic ───────────────────────────────────────────────────────────────

def find_due_followups(leads, already_sent):
    """Find leads that need Day 2 or Day 5 follow-ups."""
    day2_queue = []
    day5_queue = []
    allow_local_smb = os.environ.get(ALLOW_LOCAL_SMB_FOLLOWUPS_ENV, "").lower() in {
        "1", "true", "yes"
    }

    for email, lead in leads.items():
        stages = lead["stages"]

        # Skip if disqualified
        if stages & SKIP_STAGES:
            continue
        if not allow_local_smb and lead.get("category") in PAUSED_LOCAL_SMB_CATEGORIES:
            continue
        # Must have been contacted
        if "contacted" not in stages:
            continue

        age = days_since(lead["first_ts"])
        key2 = f"{email}:{DAY2_STAGE}"
        key5 = f"{email}:{DAY5_STAGE}"

        # Also check campaign-engine stages (step1_sent = their Day 2)
        has_day2 = (DAY2_STAGE in stages or "step1_sent" in stages or key2 in already_sent)
        has_day5 = (DAY5_STAGE in stages or "step2_sent" in stages or key5 in already_sent)

        if not has_day2 and DAY2_MIN <= age <= DAY2_MAX:
            day2_queue.append(lead)
        elif has_day2 and not has_day5 and DAY5_MIN <= age <= DAY5_MAX:
            day5_queue.append(lead)

    return day2_queue, day5_queue


def run_followups(dry_run=False):
    """Main execution: find and send due follow-ups."""
    load_env()

    daily_sent = get_daily_sent_count()
    if daily_sent >= DAILY_CAP:
        print(f"⛔ Daily follow-up cap reached ({daily_sent}/{DAILY_CAP}). Skipping.")
        return

    remaining = min(MAX_SENDS_PER_RUN, DAILY_CAP - daily_sent)
    print(f"📬 Follow-up budget: {daily_sent} sent today, {remaining} remaining (cap={DAILY_CAP})")

    leads = load_pipeline()
    already_sent = load_followup_log()
    day2_queue, day5_queue = find_due_followups(leads, already_sent)

    print(f"\n📨 Day 2 follow-ups due: {len(day2_queue)}")
    print(f"📨 Day 5 follow-ups due: {len(day5_queue)}")

    if dry_run:
        print("\n🔍 DRY RUN — showing what would send:\n")
        for lead in day2_queue[:5]:
            subj, body = build_day2_email(lead)
            print(f"  DAY 2 → {lead['email']} [{lead['category']}] [{days_since(lead['first_ts'])}d]")
            print(f"          Subject: {subj}")
        for lead in day5_queue[:5]:
            subj, body = build_day5_email(lead)
            print(f"  DAY 5 → {lead['email']} [{lead['category']}] [{days_since(lead['first_ts'])}d]")
            print(f"          Subject: {subj}")
        print(f"\n  ... and {max(0, len(day2_queue)-5)} more Day 2, {max(0, len(day5_queue)-5)} more Day 5")
        return

    total_sent = 0
    total_failed = 0

    # Send Day 2 follow-ups first (higher priority — fresher leads)
    print("\n─── Day 2 Follow-ups ───")
    for lead in day2_queue:
        if total_sent >= remaining:
            print(f"  ⚠️ Budget exhausted ({total_sent}/{remaining})")
            break

        subject, body = build_day2_email(lead)
        age = days_since(lead["first_ts"])
        print(f"  → {lead['email']} [{lead['category']}] [{age}d]", end=" ")

        ok, detail = send_email(lead["email"], subject, body)
        if ok:
            log_followup(lead, DAY2_STAGE, "sent", detail)
            print("✅")
            total_sent += 1
        else:
            log_followup(lead, DAY2_STAGE, "failed", detail)
            print(f"❌ {detail}")
            total_failed += 1
            if "429" in str(detail) or "daily_quota" in str(detail):
                print("  ⛔ Rate limited — stopping.")
                break

        time.sleep(DELAY_BETWEEN_SENDS)

    # Send Day 5 follow-ups
    print("\n─── Day 5 Follow-ups ───")
    for lead in day5_queue:
        if total_sent >= remaining:
            print(f"  ⚠️ Budget exhausted ({total_sent}/{remaining})")
            break

        subject, body = build_day5_email(lead)
        age = days_since(lead["first_ts"])
        print(f"  → {lead['email']} [{lead['category']}] [{age}d]", end=" ")

        ok, detail = send_email(lead["email"], subject, body)
        if ok:
            log_followup(lead, DAY5_STAGE, "sent", detail)
            print("✅")
            total_sent += 1
        else:
            log_followup(lead, DAY5_STAGE, "failed", detail)
            print(f"❌ {detail}")
            total_failed += 1
            if "429" in str(detail) or "daily_quota" in str(detail):
                print("  ⛔ Rate limited — stopping.")
                break

        time.sleep(DELAY_BETWEEN_SENDS)

    print(f"\n📊 Summary: {total_sent} sent, {total_failed} failed")
    print(f"   Day 2 remaining: {len(day2_queue) - min(total_sent, len(day2_queue))}")
    print(f"   Day 5 remaining: {len(day5_queue)}")


def show_stats():
    """Show follow-up pipeline statistics."""
    load_env()
    leads = load_pipeline()
    already_sent = load_followup_log()

    contacted = 0
    day2_due = 0
    day2_waiting = 0
    day2_done = 0
    day5_due = 0
    day5_waiting = 0
    day5_done = 0
    skipped = 0

    for email, lead in leads.items():
        stages = lead["stages"]
        if stages & SKIP_STAGES:
            skipped += 1
            continue
        if "contacted" not in stages:
            continue

        contacted += 1
        age = days_since(lead["first_ts"])
        key2 = f"{email}:{DAY2_STAGE}"
        key5 = f"{email}:{DAY5_STAGE}"

        has_day2 = (DAY2_STAGE in stages or "step1_sent" in stages or key2 in already_sent)
        has_day5 = (DAY5_STAGE in stages or "step2_sent" in stages or key5 in already_sent)

        if has_day2:
            day2_done += 1
        elif age < DAY2_MIN:
            day2_waiting += 1
        elif DAY2_MIN <= age <= DAY2_MAX:
            day2_due += 1

        if has_day5:
            day5_done += 1
        elif has_day2 and age < DAY5_MIN:
            day5_waiting += 1
        elif has_day2 and DAY5_MIN <= age <= DAY5_MAX:
            day5_due += 1

    daily_sent = get_daily_sent_count()

    print("📊 Follow-up Pipeline:")
    print(f"  Total contacted: {contacted}")
    print(f"  Skipped (opted out/bounced): {skipped}")
    print(f"")
    print(f"  Day 2 follow-up:")
    print(f"    ✅ Sent: {day2_done}")
    print(f"    🔜 Due now: {day2_due}")
    print(f"    ⏳ Waiting (< 2 days): {day2_waiting}")
    print(f"")
    print(f"  Day 5 follow-up:")
    print(f"    ✅ Sent: {day5_done}")
    print(f"    🔜 Due now: {day5_due}")
    print(f"    ⏳ Waiting (< 5 days): {day5_waiting}")
    print(f"")
    print(f"  Today's sends: {daily_sent}/{DAILY_CAP}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Follow-up email automation for meetrick.ai")
    parser.add_argument("--run", action="store_true", help="Send all due follow-ups")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would send")
    parser.add_argument("--stats", action="store_true", help="Show follow-up pipeline stats")
    args = parser.parse_args()

    if args.run:
        run_followups(dry_run=False)
    elif args.dry_run:
        run_followups(dry_run=True)
    elif args.stats:
        show_stats()
    else:
        show_stats()
