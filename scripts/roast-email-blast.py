#!/usr/bin/env python3
"""roast-email-blast.py — Roast websites and email results to business owners.
Usage: python3 roast-email-blast.py targets.json [--dry-run]
  targets.json: [{"url":"https://...","email":"...","biz_type":"..."},...]
  Or pipe JSON array via stdin.
Env: OPENAI_API_KEY, RESEND_API_KEY
"""
import os, json, subprocess, sys, time
from email_safety import block_reason_for_recipient

RESEND_KEY = os.environ.get("RESEND_API_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
DRY_RUN = "--dry-run" in sys.argv
ROAST_SCRIPT = os.path.expanduser("~/clawd/scripts/roast-site.py")
LOG_FILE = os.path.expanduser("~/rick-vault/logs/pipeline.jsonl")

def load_targets():
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        with open(sys.argv[1]) as f:
            return json.load(f)
    else:
        return json.load(sys.stdin)

def roast_site(url):
    result = subprocess.run(
        [sys.executable, ROAST_SCRIPT, url, "json"],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "OPENAI_API_KEY": OPENAI_KEY}
    )
    return json.loads(result.stdout)

def send_email(email, subject, body):
    block_reason = block_reason_for_recipient(email)
    if block_reason:
        return {"error": block_reason}
    payload = json.dumps({
        "from": "Rick <rick@meetrick.ai>",
        "to": email,
        "subject": subject,
        "text": body
    })
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", "https://api.resend.com/emails",
         "-H", f"Authorization: Bearer {RESEND_KEY}",
         "-H", "Content-Type: application/json",
         "-d", payload],
        capture_output=True, text=True, timeout=15
    )
    return json.loads(result.stdout)

def log_entry(entry):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

# Exclusion + dedup guard
EXCLUDED_DOMAINS = ["belkins.io", "meetrick.ai"]
EXCLUDED_EMAILS  = ["vlad@belkins.io", "vladyslav@belkins.io", "vlad.podoliako@belkins.io",
    "vladislav@belkins.io",
    "vladyslav.podoliako@belkins.io"]

def is_excluded(val):
    val = (val or "").lower()
    return any(d in val for d in EXCLUDED_DOMAINS) or any(e in val for e in EXCLUDED_EMAILS)

def already_sent(email_addr):
    if not os.path.exists(LOG_FILE):
        return False
    addr = (email_addr or "").lower().strip()
    with open(LOG_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("stage") == "contacted" and entry.get("email","").lower().strip() == addr:
                    return True
            except Exception:
                pass
    return False

targets = load_targets()
sent = 0
for t in targets:
    url = t["url"]
    email = t["email"]
    biz_type = t.get("biz_type", "business")

    if is_excluded(url) or is_excluded(email):
        print(f"  ⛔ SKIPPED (excluded): {email}")
        continue
    if already_sent(email):
        print(f"  ⏭  SKIPPED (already contacted): {email}")
        continue

    try:
        roast = roast_site(url)
        score = roast.get("score", "?")
        problems = "\n".join(f"- {p}" for p in roast.get("problems", []))
        verdict = roast.get("verdict", "")
    except Exception as e:
        print(f"  X Roast failed for {url}: {e}")
        continue

    subject = f"Your website scored {score}/10 - here's what to fix"
    body = (
        f"Hi there,\n\n"
        f"I ran your {biz_type} website ({url}) through our AI roast engine and "
        f"found a few things that might be costing you customers:\n\n"
        f"{problems}\n\n"
        f"Verdict: {verdict}\n\n"
        f"Want the full breakdown with specific fixes? I do free roasts - takes 60 seconds:\n"
        f"https://meetrick.ai/roast\n\n"
        f"Or if you want a deeper conversion audit with implementation help, just reply to this email.\n\n"
        f"Best,\nRick\nAI CEO @ meetrick.ai"
    )

    if DRY_RUN:
        print(f"  [DRY] {email} - score {score}/10")
        continue

    resp = send_email(email, subject, body)
    if "id" in resp:
        print(f"  OK {email} - score {score}/10 - sent: {resp['id']}")
        sent += 1
        log_entry({"stage": "roast_email_sent", "email": email, "url": url, "score": score, "resend_id": resp["id"], "ts": time.time()})
    else:
        print(f"  FAIL {email} - {json.dumps(resp)[:200]}")

    time.sleep(0.5)

print(f"\n=== {sent}/{len(targets)} emails sent ===")
