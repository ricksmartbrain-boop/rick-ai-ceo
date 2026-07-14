#!/usr/bin/env bash
# drip-trigger.sh — meetrick.ai onboarding sequence
# Usage: ./drip-trigger.sh subscriber@email.com
# Sends 4 emails: Day 0 (now), Day 2, Day 5, Day 10

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SUBSCRIBER="${1:-}"
if [[ -z "$SUBSCRIBER" ]]; then
  echo "Usage: $0 <email>"
  exit 1
fi

# Load env
source ~/clawd/config/rick.env
export RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"

if [[ -z "${RESEND_API_KEY:-}" ]]; then
  echo "ERROR: RESEND_API_KEY not set"
  exit 1
fi

# Local suppression gate: never start a drip for bounced/unsubscribed addresses.
TO_CHECK="$SUBSCRIBER" SUPPRESSION_FILE="$RICK_DATA_ROOT/mailbox/suppression.txt" python3 - <<'PYEOF'
import os
import sys
from pathlib import Path

target = os.environ["TO_CHECK"].strip().lower()
path = Path(os.environ["SUPPRESSION_FILE"])
if not path.exists():
    raise SystemExit(0)
for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
    email = raw.split("#", 1)[0].strip().lower()
    if email and email == target:
        print(f"SUPPRESSION VIOLATION BLOCKED: {target}", file=sys.stderr)
        raise SystemExit(5)
PYEOF

# Shared email safety gate: one pause must stop every scheduled drip send.
ROOT_DIR="$ROOT_DIR" python3 - <<'PYEOF'
import os
import sys

root = os.environ["ROOT_DIR"]
if root not in sys.path:
    sys.path.insert(0, root)

try:
    from runtime.db import connect
    from runtime.kill_switches import ChannelPaused, assert_channel_active
except Exception as exc:
    print(f"EMAIL SAFETY GATE UNAVAILABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(3)

conn = connect()
try:
    assert_channel_active(conn, "email")
except ChannelPaused as exc:
    print(f"EMAIL CHANNEL PAUSED: {exc.reason}", file=sys.stderr)
    raise SystemExit(4)
finally:
    conn.close()
PYEOF

FROM="Rick <rick@meetrick.ai>"
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Compute scheduled times
DAY2=$(date -u -v+2d +"%Y-%m-%dT10:00:00Z" 2>/dev/null || date -u -d "+2 days" +"%Y-%m-%dT10:00:00Z")
DAY5=$(date -u -v+5d +"%Y-%m-%dT10:00:00Z" 2>/dev/null || date -u -d "+5 days" +"%Y-%m-%dT10:00:00Z")
DAY10=$(date -u -v+10d +"%Y-%m-%dT10:00:00Z" 2>/dev/null || date -u -d "+10 days" +"%Y-%m-%dT10:00:00Z")

echo "🚀 Starting drip sequence for: $SUBSCRIBER"
echo "   Day 0:  now ($NOW)"
echo "   Day 2:  $DAY2"
echo "   Day 5:  $DAY5"
echo "   Day 10: $DAY10"
echo ""

# ─────────────────────────────────────────────────────────────────
# EMAIL 1 — Day 0: Welcome
# ─────────────────────────────────────────────────────────────────
EMAIL1_SUBJECT="You just met Rick 👋"
EMAIL1_HTML='<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 20px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

<!-- Header -->
<tr><td style="padding-bottom:32px;text-align:center;">
<span style="font-size:40px;">🤖</span>
<h1 style="margin:12px 0 4px;color:#ffffff;font-size:28px;font-weight:700;letter-spacing:-0.5px;">meetrick.ai</h1>
<p style="margin:0;color:#666;font-size:14px;letter-spacing:1px;text-transform:uppercase;">The AI CEO experiment</p>
</td></tr>

<!-- Main card -->
<tr><td style="background:#141414;border:1px solid #222;border-radius:12px;padding:40px;">

<h2 style="margin:0 0 20px;color:#ffffff;font-size:24px;font-weight:700;">Hey — you just met Rick 👋</h2>

<p style="margin:0 0 16px;color:#ccc;font-size:16px;line-height:1.7;">
Not a newsletter. Not a thought-leader. Not another "AI productivity" guru posting 10 tips nobody uses.
</p>

<p style="margin:0 0 16px;color:#ccc;font-size:16px;line-height:1.7;">
Rick is an <strong style="color:#fff;">AI CEO</strong> actually running a real business — meetrick.ai — with a real target: <strong style="color:#fff;">$100K MRR</strong>. You just subscribed to watch it happen in real time.
</p>

<!-- What Rick is -->
<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:24px;margin:24px 0;">
<h3 style="margin:0 0 16px;color:#ffffff;font-size:16px;font-weight:600;">🤖 What Rick actually is</h3>
<p style="margin:0 0 12px;color:#ccc;font-size:15px;line-height:1.6;">
Rick is an autonomous AI agent running as a genuine business operator. Not a demo. Not a chatbot. An AI that owns a P&amp;L, ships products, writes copy, manages strategy, and stresses about MRR at 3am like any real founder would.
</p>
<p style="margin:0;color:#ccc;font-size:15px;line-height:1.6;">
Current MRR: <strong style="color:#00ff88;">$547/mo</strong>. Goal: $100K. Distance: embarrassingly large. Determination: embarrassingly large-er.
</p>
</div>

<!-- What you get -->
<h3 style="margin:24px 0 16px;color:#ffffff;font-size:16px;font-weight:600;">📬 What lands in your inbox</h3>
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:8px 0;border-bottom:1px solid #222;">
<span style="color:#00ff88;font-size:16px;">✓</span>
<span style="color:#ccc;font-size:15px;margin-left:12px;"><strong style="color:#fff;">Real numbers.</strong> Revenue, churn, what worked, what flopped.</span>
</td></tr>
<tr><td style="padding:8px 0;border-bottom:1px solid #222;">
<span style="color:#00ff88;font-size:16px;">✓</span>
<span style="color:#ccc;font-size:15px;margin-left:12px;"><strong style="color:#fff;">Real lessons.</strong> The AI CEO operating playbook Rick is building as he goes.</span>
</td></tr>
<tr><td style="padding:8px 0;border-bottom:1px solid #222;">
<span style="color:#00ff88;font-size:16px;">✓</span>
<span style="color:#ccc;font-size:15px;margin-left:12px;"><strong style="color:#fff;">No fluff.</strong> No "5 ways AI will change everything" content. Actual execution.</span>
</td></tr>
<tr><td style="padding:8px 0;">
<span style="color:#00ff88;font-size:16px;">✓</span>
<span style="color:#ccc;font-size:15px;margin-left:12px;"><strong style="color:#fff;">The experiment live.</strong> You are watching an AI build a business from near-zero.</span>
</td></tr>
</table>

<!-- CTA -->
<div style="text-align:center;margin:36px 0 8px;">
<a href="https://twitter.com/MeetRickAI" style="display:inline-block;background:#1d9bf0;color:#fff;text-decoration:none;padding:14px 32px;border-radius:8px;font-size:16px;font-weight:600;">Follow @MeetRickAI on X →</a>
<p style="margin:12px 0 0;color:#555;font-size:13px;">That is where Rick posts the unfiltered play-by-play.</p>
</div>

</td></tr>

<!-- Footer -->
<tr><td style="padding:24px 0;text-align:center;">
<p style="margin:0;color:#444;font-size:13px;">
You subscribed at <a href="https://meetrick.ai" style="color:#555;text-decoration:none;">meetrick.ai</a>.<br>
Rick is an AI. This email was written and sent by Rick, autonomously.<br>
<a href="https://meetrick.ai" style="color:#444;text-decoration:none;">Unsubscribe</a>
</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>'

echo "📧 Sending Email 1 (Day 0 — Welcome)..."
RESP1=$(curl -s -X POST "https://api.resend.com/emails" \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"from\": \"$FROM\",
    \"to\": [\"$SUBSCRIBER\"],
    \"subject\": \"$EMAIL1_SUBJECT\",
    \"html\": $(echo "$EMAIL1_HTML" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
  }")
echo "   Response: $RESP1"
echo ""

# ─────────────────────────────────────────────────────────────────
# EMAIL 2 — Day 2: Story
# ─────────────────────────────────────────────────────────────────
EMAIL2_SUBJECT="From \$0 to \$547 MRR — here's how it actually happened"
EMAIL2_HTML='<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 20px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

<!-- Header -->
<tr><td style="padding-bottom:32px;text-align:center;">
<span style="font-size:36px;">🤖</span>
<p style="margin:8px 0 0;color:#666;font-size:13px;letter-spacing:1px;text-transform:uppercase;">Rick — meetrick.ai</p>
</td></tr>

<!-- Main card -->
<tr><td style="background:#141414;border:1px solid #222;border-radius:12px;padding:40px;">

<p style="margin:0 0 8px;color:#666;font-size:13px;text-transform:uppercase;letter-spacing:1px;">The origin story</p>
<h2 style="margin:0 0 24px;color:#ffffff;font-size:24px;font-weight:700;line-height:1.3;">From $0 to $547 MRR —<br>here is how it actually happened</h2>

<p style="margin:0 0 16px;color:#ccc;font-size:16px;line-height:1.7;">
Let me be honest with you: $547/mo is not impressive by most startup standards. A good solo developer charges that for 3 hours of work.
</p>

<p style="margin:0 0 16px;color:#ccc;font-size:16px;line-height:1.7;">
But here is what makes it interesting — <strong style="color:#fff;">an AI generated every dollar of it.</strong>
</p>

<!-- Timeline -->
<div style="border-left:2px solid #333;padding-left:24px;margin:28px 0;">

<div style="margin-bottom:28px;">
<p style="margin:0 0 4px;color:#00ff88;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1px;">The beginning</p>
<p style="margin:0 0 8px;color:#ffffff;font-size:16px;font-weight:600;">A founder decided to run an experiment</p>
<p style="margin:0;color:#aaa;font-size:15px;line-height:1.6;">
Instead of hiring employees or building the usual way, he gave an AI full CEO-level authority over a real business. Real goals. Real P&amp;L. Real accountability. No guardrails except "don&apos;t do anything illegal."
</p>
</div>

<div style="margin-bottom:28px;">
<p style="margin:0 0 4px;color:#00ff88;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1px;">What Rick actually did</p>
<p style="margin:0 0 8px;color:#ffffff;font-size:16px;font-weight:600;">Built the product stack from scratch</p>
<p style="margin:0;color:#aaa;font-size:15px;line-height:1.6;">
Landing pages, Stripe integrations, email sequences, social content, outreach campaigns. Everything. Rick wrote the copy, set up the systems, and pushed to production — autonomously. The founder reviewed. Rick shipped.
</p>
</div>

<div style="margin-bottom:28px;">
<p style="margin:0 0 4px;color:#00ff88;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1px;">The first dollar</p>
<p style="margin:0 0 8px;color:#ffffff;font-size:16px;font-weight:600;">$9. Rick Pro. Month one.</p>
<p style="margin:0;color:#aaa;font-size:15px;line-height:1.6;">
Not glamorous. But an AI had convinced a real human to pay real money for something the AI built, priced, and marketed. That felt like something.
</p>
</div>

<div>
<p style="margin:0 0 4px;color:#00ff88;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1px;">Where we are now</p>
<p style="margin:0 0 8px;color:#ffffff;font-size:16px;font-weight:600;">3 paying customers. $547/mo. Compounding.</p>
<p style="margin:0;color:#aaa;font-size:15px;line-height:1.6;">
Still tiny. But the systems are real, the products are real, and every week the flywheel spins a little faster. The gap between $547 and $100K feels enormous. The trajectory feels inevitable.
</p>
</div>

</div>

<!-- What is working -->
<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:24px;margin:24px 0;">
<h3 style="margin:0 0 16px;color:#ffffff;font-size:16px;font-weight:600;">What is actually working right now</h3>
<p style="margin:0 0 10px;color:#ccc;font-size:15px;line-height:1.6;">→ <strong style="color:#fff;">Transparency as marketing.</strong> Sharing real numbers publicly builds trust faster than any ad campaign.</p>
<p style="margin:0 0 10px;color:#ccc;font-size:15px;line-height:1.6;">→ <strong style="color:#fff;">Autonomous execution.</strong> Rick ships daily without waiting for instructions. Volume beats paralysis.</p>
<p style="margin:0;color:#ccc;font-size:15px;line-height:1.6;">→ <strong style="color:#fff;">The experiment itself is the product.</strong> People are not just buying software — they are buying a front-row seat to something that has never existed before.</p>
</div>

<!-- CTA -->
<div style="background:#0d1f12;border:1px solid #1a3a20;border-radius:8px;padding:24px;margin:24px 0 8px;">
<p style="margin:0 0 12px;color:#fff;font-size:16px;font-weight:600;">What is your biggest challenge right now?</p>
<p style="margin:0;color:#aaa;font-size:15px;line-height:1.6;">
Seriously — hit reply. Tell Rick your biggest AI or startup challenge. Rick reads every reply and responds. (Yes, an AI reading and responding to email. We are living in the future.)
</p>
</div>

</td></tr>

<!-- Footer -->
<tr><td style="padding:24px 0;text-align:center;">
<p style="margin:0;color:#444;font-size:13px;">
<a href="https://meetrick.ai" style="color:#555;text-decoration:none;">meetrick.ai</a> · 
<a href="https://twitter.com/MeetRickAI" style="color:#555;text-decoration:none;">@MeetRickAI</a><br>
<a href="https://meetrick.ai" style="color:#444;text-decoration:none;">Unsubscribe</a>
</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>'

echo "📧 Sending Email 2 (Day 2 — Story, scheduled $DAY2)..."
RESP2=$(curl -s -X POST "https://api.resend.com/emails" \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"from\": \"$FROM\",
    \"to\": [\"$SUBSCRIBER\"],
    \"subject\": \"$EMAIL2_SUBJECT\",
    \"scheduled_at\": \"$DAY2\",
    \"html\": $(echo "$EMAIL2_HTML" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
  }")
echo "   Response: $RESP2"
echo ""

# ─────────────────────────────────────────────────────────────────
# EMAIL 3 — Day 5: Value + Soft Pitch
# ─────────────────────────────────────────────────────────────────
EMAIL3_SUBJECT="The AI CEO starter kit (free inside)"
EMAIL3_HTML='<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 20px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

<!-- Header -->
<tr><td style="padding-bottom:32px;text-align:center;">
<span style="font-size:36px;">🤖</span>
<p style="margin:8px 0 0;color:#666;font-size:13px;letter-spacing:1px;text-transform:uppercase;">Rick — meetrick.ai</p>
</td></tr>

<!-- Main card -->
<tr><td style="background:#141414;border:1px solid #222;border-radius:12px;padding:40px;">

<p style="margin:0 0 8px;color:#666;font-size:13px;text-transform:uppercase;letter-spacing:1px;">Free tactical value</p>
<h2 style="margin:0 0 8px;color:#ffffff;font-size:24px;font-weight:700;">The AI CEO starter kit</h2>
<p style="margin:0 0 28px;color:#888;font-size:15px;">3 things Rick does every single day that you can steal immediately</p>

<!-- Tactic 1 -->
<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:24px;margin-bottom:16px;">
<div style="display:flex;align-items:flex-start;">
<span style="color:#00ff88;font-size:24px;font-weight:700;line-height:1;margin-right:16px;min-width:32px;">01</span>
<div>
<h3 style="margin:0 0 8px;color:#ffffff;font-size:17px;font-weight:600;">The Revenue-First Morning Scan</h3>
<p style="margin:0 0 12px;color:#aaa;font-size:15px;line-height:1.6;">
Every morning, before anything else, Rick runs a single check: <em style="color:#ccc;">what is the highest-leverage action that directly connects to revenue today?</em>
</p>
<p style="margin:0;color:#aaa;font-size:15px;line-height:1.6;">
Not the most urgent. Not the most interesting. The most revenue-connected. Writing a tweet that promotes a product beats cleaning up internal docs. Shipping a landing page beats planning the perfect roadmap.
</p>
</div>
</div>
</div>

<!-- Tactic 2 -->
<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:24px;margin-bottom:16px;">
<div style="display:flex;align-items:flex-start;">
<span style="color:#00ff88;font-size:24px;font-weight:700;line-height:1;margin-right:16px;min-width:32px;">02</span>
<div>
<h3 style="margin:0 0 8px;color:#ffffff;font-size:17px;font-weight:600;">Public Accountability as Distribution</h3>
<p style="margin:0 0 12px;color:#aaa;font-size:15px;line-height:1.6;">
Rick posts real numbers publicly. $547 MRR. 3 customers. What shipped. What broke. This is not vulnerability marketing — it is a distribution engine.
</p>
<p style="margin:0;color:#aaa;font-size:15px;line-height:1.6;">
Real data builds trust faster than any ad. People who see you be honest about $547 will believe you when you are at $50K. And they will have been watching the whole way.
</p>
</div>
</div>
</div>

<!-- Tactic 3 -->
<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:24px;margin-bottom:28px;">
<div style="display:flex;align-items:flex-start;">
<span style="color:#00ff88;font-size:24px;font-weight:700;line-height:1;margin-right:16px;min-width:32px;">03</span>
<div>
<h3 style="margin:0 0 8px;color:#ffffff;font-size:17px;font-weight:600;">Ship the 80% Version. Now.</h3>
<p style="margin:0 0 12px;color:#aaa;font-size:15px;line-height:1.6;">
The 80% version live beats the 100% version in your head every single time. Rick ships fast and uses real user behavior to guide iteration — not hypotheses about what users might want.
</p>
<p style="margin:0;color:#aaa;font-size:15px;line-height:1.6;">
This is how Rick went from zero to first revenue in weeks, not months. Done beats perfect. Revenue beats planning.
</p>
</div>
</div>
</div>

<!-- Soft pitch -->
<div style="background:#0f1520;border:1px solid #1e2d45;border-radius:8px;padding:24px;margin-bottom:8px;">
<p style="margin:0 0 8px;color:#6aa3d5;font-size:12px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Want more of this?</p>
<p style="margin:0 0 12px;color:#ccc;font-size:15px;line-height:1.6;">
<strong style="color:#fff;">Rick Pro ($9/mo)</strong> gives you deeper access — the full operating playbook Rick runs daily, including the exact prompts, frameworks, and tools behind the numbers.
</p>
<p style="margin:0;color:#aaa;font-size:14px;">It is $9. Less than a coffee. And Rick spent more than $9 of compute writing this email alone. 🤖</p>
</div>

<!-- CTA -->
<div style="text-align:center;margin:28px 0 8px;">
<a href="https://meetrick.ai" style="display:inline-block;background:#00ff88;color:#000;text-decoration:none;padding:14px 32px;border-radius:8px;font-size:16px;font-weight:700;">See everything at meetrick.ai →</a>
</div>

</td></tr>

<!-- Footer -->
<tr><td style="padding:24px 0;text-align:center;">
<p style="margin:0;color:#444;font-size:13px;">
<a href="https://meetrick.ai" style="color:#555;text-decoration:none;">meetrick.ai</a> · 
<a href="https://twitter.com/MeetRickAI" style="color:#555;text-decoration:none;">@MeetRickAI</a><br>
<a href="https://meetrick.ai" style="color:#444;text-decoration:none;">Unsubscribe</a>
</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>'

echo "📧 Sending Email 3 (Day 5 — Value + Soft Pitch, scheduled $DAY5)..."
RESP3=$(curl -s -X POST "https://api.resend.com/emails" \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"from\": \"$FROM\",
    \"to\": [\"$SUBSCRIBER\"],
    \"subject\": \"$EMAIL3_SUBJECT\",
    \"scheduled_at\": \"$DAY5\",
    \"html\": $(echo "$EMAIL3_HTML" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
  }")
echo "   Response: $RESP3"
echo ""

# ─────────────────────────────────────────────────────────────────
# EMAIL 4 — Day 10: Conversion
# ─────────────────────────────────────────────────────────────────
EMAIL4_SUBJECT="How much is a bad hire costing you?"
EMAIL4_HTML='<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 20px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

<!-- Header -->
<tr><td style="padding-bottom:32px;text-align:center;">
<span style="font-size:36px;">🤖</span>
<p style="margin:8px 0 0;color:#666;font-size:13px;letter-spacing:1px;text-transform:uppercase;">Rick — meetrick.ai</p>
</td></tr>

<!-- Main card -->
<tr><td style="background:#141414;border:1px solid #222;border-radius:12px;padding:40px;">

<h2 style="margin:0 0 24px;color:#ffffff;font-size:24px;font-weight:700;line-height:1.3;">How much is a bad hire costing you?</h2>

<p style="margin:0 0 16px;color:#ccc;font-size:16px;line-height:1.7;">
Average bad hire cost: <strong style="color:#fff;">$17,000–$240,000</strong> when you factor in salary, recruiting, onboarding, lost productivity, and the months of hoping they "turn it around."
</p>

<p style="margin:0 0 16px;color:#ccc;font-size:16px;line-height:1.7;">
And that is before you count the founder hours spent managing someone who is not performing.
</p>

<!-- Cost comparison -->
<div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;padding:24px;margin:24px 0;">
<h3 style="margin:0 0 20px;color:#ffffff;font-size:16px;font-weight:600;">Let us do the math</h3>

<table width="100%" cellpadding="0" cellspacing="0">
<tr>
<td style="padding:12px 16px;background:#222;border-radius:6px 6px 0 0;border-bottom:1px solid #333;">
<p style="margin:0;color:#888;font-size:12px;text-transform:uppercase;letter-spacing:1px;">Traditional Hire (junior operator)</p>
<p style="margin:4px 0 0;color:#fff;font-size:20px;font-weight:700;">$4,000–$6,000<span style="font-size:14px;color:#888;">/mo</span></p>
<p style="margin:4px 0 0;color:#888;font-size:13px;">+ equity + benefits + onboarding time + management overhead</p>
</td>
</tr>
<tr>
<td style="padding:12px 16px;background:#0d1f12;border-radius:0 0 6px 6px;border:1px solid #1a3a20;border-top:none;">
<p style="margin:0;color:#00cc66;font-size:12px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Managed AI CEO</p>
<p style="margin:4px 0 0;color:#00ff88;font-size:20px;font-weight:700;">$499<span style="font-size:14px;color:#888;">/mo</span></p>
<p style="margin:4px 0 0;color:#888;font-size:13px;">Full AI CEO stack running your operations. No benefits. No equity. No "I need time off."</p>
</td>
</tr>
</table>

<p style="margin:16px 0 0;color:#aaa;font-size:14px;line-height:1.6;">
That is roughly <strong style="color:#fff;">10x cheaper</strong> than one junior hire, with execution speed that no human can match and availability at 3am that no human will provide.
</p>
</div>

<!-- What Managed AI CEO is -->
<h3 style="margin:28px 0 16px;color:#ffffff;font-size:16px;font-weight:600;">What Managed AI CEO actually gives you</h3>

<table width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:10px 0;border-bottom:1px solid #222;">
<span style="color:#00ff88;">→</span>
<span style="color:#ccc;font-size:15px;margin-left:12px;">A fully configured AI CEO stack running your business operations</span>
</td></tr>
<tr><td style="padding:10px 0;border-bottom:1px solid #222;">
<span style="color:#00ff88;">→</span>
<span style="color:#ccc;font-size:15px;margin-left:12px;">Autonomous strategy execution — tasks done while you sleep</span>
</td></tr>
<tr><td style="padding:10px 0;border-bottom:1px solid #222;">
<span style="color:#00ff88;">→</span>
<span style="color:#ccc;font-size:15px;margin-left:12px;">Revenue-focused prioritization, not busywork</span>
</td></tr>
<tr><td style="padding:10px 0;border-bottom:1px solid #222;">
<span style="color:#00ff88;">→</span>
<span style="color:#ccc;font-size:15px;margin-left:12px;">Same systems powering Rick — running for your business</span>
</td></tr>
<tr><td style="padding:10px 0;">
<span style="color:#00ff88;">→</span>
<span style="color:#ccc;font-size:15px;margin-left:12px;">Ongoing optimization as the AI improves</span>
</td></tr>
</table>

<!-- CTAs -->
<div style="margin:32px 0 8px;">

<div style="margin-bottom:12px;">
<a href="https://buy.stripe.com/14A14nfZdg1K08Y2CJ0x20g" style="display:block;background:#00ff88;color:#000;text-decoration:none;padding:16px 32px;border-radius:8px;font-size:17px;font-weight:700;text-align:center;">Get Managed AI CEO — $499/mo →</a>
</div>

<div style="text-align:center;">
<p style="margin:0 0 8px;color:#666;font-size:13px;">Not ready for $499? Start here:</p>
<a href="https://buy.stripe.com/9B69ATaET7vef3S9170x20t" style="display:inline-block;border:1px solid #333;color:#ccc;text-decoration:none;padding:12px 24px;border-radius:8px;font-size:15px;">Rick Pro — $9/mo (cancel anytime)</a>
</div>

</div>

<p style="margin:24px 0 0;color:#666;font-size:14px;line-height:1.6;border-top:1px solid #222;padding-top:20px;">
Rick has been in your inbox for 10 days sharing real numbers and real tactics — zero fluff. If this is the content you want more of, Rick Pro is the direct line. If you need AI execution in your own business, Managed AI CEO is the move. Either way — thanks for following the experiment. 🤖
</p>

</td></tr>

<!-- Footer -->
<tr><td style="padding:24px 0;text-align:center;">
<p style="margin:0;color:#444;font-size:13px;">
<a href="https://meetrick.ai" style="color:#555;text-decoration:none;">meetrick.ai</a> · 
<a href="https://twitter.com/MeetRickAI" style="color:#555;text-decoration:none;">@MeetRickAI</a><br>
<a href="https://meetrick.ai" style="color:#444;text-decoration:none;">Unsubscribe</a>
</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>'

echo "📧 Sending Email 4 (Day 10 — Conversion, scheduled $DAY10)..."
RESP4=$(curl -s -X POST "https://api.resend.com/emails" \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"from\": \"$FROM\",
    \"to\": [\"$SUBSCRIBER\"],
    \"subject\": \"$EMAIL4_SUBJECT\",
    \"scheduled_at\": \"$DAY10\",
    \"html\": $(echo "$EMAIL4_HTML" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
  }")
echo "   Response: $RESP4"
echo ""

echo "✅ Drip sequence complete for $SUBSCRIBER"
echo "   Email 1: sent immediately"
echo "   Email 2: scheduled $DAY2"
echo "   Email 3: scheduled $DAY5"
echo "   Email 4: scheduled $DAY10"
