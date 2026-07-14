#!/usr/bin/env python3
"""
followup-blast.py — Send Day 3 follow-up emails to contacted leads with no reply.
Uses Resend API. Skips unsubscribed, bounced, opted-out, and already-followed-up.
Logs followup_sent stage back to pipeline.jsonl.
"""

import json, os, sys, datetime, time, subprocess
from pathlib import Path

PIPELINE_LOG = Path.home() / "rick-vault/logs/pipeline.jsonl"
ENV_FILE = Path.home() / "clawd/config/rick.env"
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SUPPRESSION_FILE = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault"))) / "mailbox" / "suppression.txt"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM = "Rick <rick@meetrick.ai>"
DAILY_LIMIT = 80  # stay under Resend rate limits
DELAY_SECONDS = 1.2


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

def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k.strip(), v)

def load_leads():
    today = datetime.date.today()
    leads = {}
    skip_stages = {"unsubscribed", "optout", "bounced", "FRAUD_ALERT", "followup_sent"}
    
    for line in PIPELINE_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except:
            continue
        email = d.get("email", "")
        if not email:
            continue
        ts = str(d.get("ts") or d.get("timestamp") or "")[:10]
        stage = d.get("stage", "")
        if email not in leads:
            leads[email] = {
                "stages": set(),
                "ts": ts,
                "biz": d.get("business_name") or d.get("target") or "",
                "city": d.get("city", ""),
                "category": d.get("category", ""),
                "email": email,
            }
        leads[email]["stages"].add(stage)
        if ts and (not leads[email]["ts"] or ts < leads[email]["ts"]):
            leads[email]["ts"] = ts

    followup = []
    for email, info in leads.items():
        if info["stages"] & skip_stages:
            continue
        if "contacted" not in info["stages"]:
            continue
        try:
            sent = datetime.date.fromisoformat(info["ts"])
            days_ago = (today - sent).days
            if days_ago >= 2:
                followup.append(info | {"days_ago": days_ago})
        except:
            pass

    return sorted(followup, key=lambda x: -x["days_ago"])

def build_email(lead):
    biz = lead["biz"].replace("https://", "").replace("http://", "").strip("/") or "your business"
    cat = lead["category"] or "business"
    subject = f"Quick follow-up — {biz}"
    body = f"""Hi,

I sent you a note a couple days ago about {biz}.

The short version: most {cat} websites are leaving real money on the table — not because the service is bad, but because the site doesn't convert.

I built an AI that audits sites like yours in 60 seconds and shows you exactly where you're losing people. It's free to try.

→ meetrick.ai/roast

If the roast resonates and you want help fixing it, that's what I do. But start with the free audit — it's worth 60 seconds.

— Rick
AI CEO, meetrick.ai

P.S. Reply "stop" anytime and I won't follow up again."""
    return subject, body

def send_email(to, subject, body):
    block_reason = email_channel_block_reason()
    if block_reason:
        print(f"  EMAIL CHANNEL PAUSED: {block_reason}")
        return False
    if is_suppressed(to):
        print(f"  SUPPRESSED: {to}")
        return False
    # Unified fail-closed per-recipient gate (2026-07-13). cold=False —
    # this script only sends Day-3 follow-ups to already-contacted leads.
    try:
        root = str(WORKSPACE_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from runtime.kill_switches import is_send_allowed

        allowed, gate_reason = is_send_allowed(to, cold=False)
    except Exception as exc:
        allowed, gate_reason = False, f"gate_unavailable: {type(exc).__name__}: {exc}"
    if not allowed:
        print(f"  SEND_BLOCKED reason={gate_reason} to={to}")
        return False
    key = os.environ.get("RESEND_API_KEY", "")
    if not key:
        print("  ❌ No RESEND_API_KEY")
        return False
    payload = json.dumps({
        "from": FROM,
        "to": [to],
        "subject": subject,
        "text": body,
    })
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", "https://api.resend.com/emails",
         "-H", "Content-Type: application/json",
         "-H", f"Authorization: Bearer {key}",
         "-d", payload],
        capture_output=True, text=True, timeout=15
    )
    resp = result.stdout
    try:
        data = json.loads(resp)
        if data.get("id"):
            return True
        else:
            print(f"  ❌ Resend error: {data}")
            return False
    except:
        print(f"  ❌ Bad response: {resp[:100]}")
        return False

def log_stage(lead, stage):
    entry = {
        "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": stage,
        "email": lead["email"],
        "business_name": lead["biz"],
        "city": lead["city"],
        "category": lead["category"],
        "channel": "cold_email_followup",
    }
    with open(PIPELINE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

def main():
    load_env()
    leads = load_leads()
    print(f"📬 Follow-up eligible: {len(leads)} leads")
    
    sent = 0
    failed = 0
    skipped = 0

    for lead in leads[:DAILY_LIMIT]:
        email = lead["email"]
        subject, body = build_email(lead)
        print(f"  → {email} ({lead['biz']}) [{lead['days_ago']}d old]", end=" ")
        ok = send_email(email, subject, body)
        if ok:
            log_stage(lead, "followup_sent")
            print("✅")
            sent += 1
        else:
            log_stage(lead, "followup_error")
            print("❌")
            failed += 1
        time.sleep(DELAY_SECONDS)

    print(f"\n✅ Done: {sent} sent, {failed} failed, {skipped} skipped")
    print(f"📊 Pipeline: {PIPELINE_LOG}")

if __name__ == "__main__":
    main()
