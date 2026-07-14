#!/usr/bin/env bash
# Lead Scrape + Blast v2 — Bing via agent-browser
# Searches for local businesses, extracts emails, sends cold roast emails via Resend
set -euo pipefail

source ~/.openclaw/workspace/config/rick.env

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
PIPELINE="$HOME/rick-vault/logs/pipeline.jsonl"
RESEND_KEY="$RESEND_API_KEY"
FROM="rick@meetrick.ai"
FROM_NAME="Rick"
ROAST_URL="https://meetrick.ai/roast"
SENT=0
TARGET=12
TMPDIR=$(mktemp -d)
SKIP_DOMAINS_FILE="$TMPDIR/skip.txt"

echo "🚀 Lead Scrape + Blast — $(date)"
echo "============================================================"

# ── Build skip list from pipeline ──────────────────────────────────────────────
python3 -c "
import json, sys
domains = set()
try:
    with open('$PIPELINE') as f:
        for line in f:
            try:
                r = json.loads(line.strip())
                if r.get('domain'): domains.add(r['domain'].lower().strip())
                if r.get('email') and '@' in r.get('email',''):
                    domains.add(r['email'].split('@')[1].lower().strip())
            except: pass
except: pass
for d in sorted(domains): print(d)
" > "$SKIP_DOMAINS_FILE"

SKIP_COUNT=$(wc -l < "$SKIP_DOMAINS_FILE")
echo "📋 $SKIP_COUNT domains already in pipeline"

# ── Helper: check if domain is in skip list ────────────────────────────────────
is_skip() {
    grep -qxF "$1" "$SKIP_DOMAINS_FILE" 2>/dev/null
}

# ── Helper: search Bing and return URLs ───────────────────────────────────────
search_bing() {
    local query="$1"
    local encoded_query
    encoded_query=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$query'))")
    local url="https://www.bing.com/search?q=${encoded_query}"
    
    agent-browser open "$url" 2>/dev/null || true
    sleep 2
    agent-browser wait --load networkidle 2>/dev/null || sleep 2
    
    agent-browser snapshot --json 2>/dev/null | python3 -c "
import sys, re, json
raw = sys.stdin.read()
try:
    text = json.dumps(json.loads(raw))
except:
    text = raw

urls = re.findall(r'https?://(?!(?:www\.bing\.|bing\.|microsoft\.|go\.microsoft\.|msn\.))[a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)+(?:/[^\s\"<>]*)?', text)
seen = set()
skip = {'yelp.','facebook.','instagram.','healthgrades.','zocdoc.','vitals.','webmd.','wikipedia.','youtube.','twitter.','linkedin.','angi.','thumbtack.','ratemds.','doximity.','google.','schema.','w3.org','gstatic.','googleapis.','tebra.com','practicefusion.','drchrono.','athena','kareo.','webflow.','wix.','squarespace.','shopify.','godaddy.','wordpress.'}
for u in urls:
    dm = re.match(r'https?://(?:www\.)?([^/?#]+)', u)
    if dm:
        d = dm.group(1).lower()
        if d not in seen and not any(x in d for x in skip) and '.' in d:
            seen.add(d)
            # Print clean base URL
            print(f'https://{dm.group(1)}')
    if len(seen) >= 5:
        break
" 2>/dev/null
}

# ── Helper: extract email from website ────────────────────────────────────────
get_email() {
    local url="$1"
    python3 -c "
import urllib.request, re, sys
url = '$url'
email = None
for path in ['', '/contact', '/contact-us', '/about']:
    try:
        req = urllib.request.Request(url.rstrip('/') + path,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'})
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode('utf-8', errors='ignore')
        emails = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', html)
        bad = ['sentry','wixpress','schema','example','placeholder','noreply','no-reply','donotreply','wordpress','gravatar','jquery','.png','.jpg','.gif']
        for e in emails:
            if not any(b in e.lower() for b in bad) and len(e) < 60 and '.' in e.split('@')[1]:
                email = e
                break
        if email:
            break
    except:
        pass
print(email or '')
" 2>/dev/null
}

# ── Helper: send email via Resend ──────────────────────────────────────────────
send_email() {
    local to="$1"
    local subject="$2"
    local body="$3"

    TO_CHECK="$to" SUPPRESSION_FILE="$RICK_DATA_ROOT/mailbox/suppression.txt" python3 - <<'PYEOF'
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
    
    python3 -c "
import json, urllib.request, urllib.error, sys
payload = json.dumps({
    'from': '$FROM_NAME <$FROM>',
    'to': ['$to'],
    'subject': '''$subject''',
    'text': '''$body''',
}).encode()
req = urllib.request.Request('https://api.resend.com/emails', data=payload,
    headers={'Authorization': 'Bearer $RESEND_KEY', 'Content-Type': 'application/json'}, method='POST')
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        result = json.loads(r.read())
        print(result.get('id',''))
except urllib.error.HTTPError as e:
    sys.stderr.write(f'HTTP {e.code}: {e.read().decode()[:200]}\n')
    print('')
except Exception as e:
    sys.stderr.write(str(e) + '\n')
    print('')
" 2>/dev/null
}

# ── Helper: log to pipeline ────────────────────────────────────────────────────
log_pipeline() {
    local biz="$1" cat="$2" city="$3" state="$4" email="$5" domain="$6" website="$7" resend_id="$8" confirmed="$9"
    python3 -c "
import json
from datetime import datetime, timezone
entry = {
    'ts': datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
    'stage': 'contacted',
    'status': 'sent',
    'business_name': '$biz',
    'category': '$cat',
    'city': '$city',
    'state': '$state',
    'email': '$email',
    'email_confirmed': $confirmed,
    'domain': '$domain',
    'website': '$website',
    'source': 'cold_outreach_blast',
    'resend_id': '$resend_id',
}
print(json.dumps(entry))
" >> "$PIPELINE"
}

# ── Process one business ───────────────────────────────────────────────────────
process_business() {
    local url="$1" city="$2" state="$3" cat_key="$4"
    
    # Extract domain
    local domain
    domain=$(python3 -c "import re; m=re.match(r'https?://(?:www\.)?([^/?#]+)',\"$url\"); print(m.group(1).lower() if m else '')")
    
    if [ -z "$domain" ]; then return; fi
    
    # Skip if already contacted
    if is_skip "$domain"; then
        echo "  ⏭️  Already contacted: $domain"
        return
    fi
    
    echo "  🌐 Checking: $url"
    
    # Get email
    local email
    email=$(get_email "$url")
    local confirmed="false"
    
    if [ -n "$email" ]; then
        confirmed="true"
        echo "  ✉️  Found: $email"
    else
        email="info@${domain}"
        echo "  📧 Using guessed: $email"
    fi
    
    # Build business name from domain
    local biz_name
    biz_name=$(python3 -c "
import re
d = '$domain'.split('.')[0]
d = re.sub(r'[-_]', ' ', d).title()
print(d if d.lower() not in ['info','www','contact','home'] else '$city Business')
")
    
    # Build email content by category
    local subject body
    case "$cat_key" in
        dermatologist)
            subject="Your $city dermatology website — honest feedback"
            body="Hi ${biz_name} team,

I was looking at dermatology practices in $city and came across your site.

Here's the honest take: patients searching for a dermatologist in $city right now are choosing between you and 3-4 competitors based almost entirely on their first 10 seconds on your website. Most practices lose 20-30% of potential bookings to fixable site issues they never think about.

I built a free tool that gives you a straight roast — no sugarcoating, no agency pitch — just honest feedback on what's working and what's costing you appointments:

👉 $ROAST_URL

Drop your URL, get a real scorecard in 60 seconds. No signup required.

Worth a look,
Rick
AI CEO, meetrick.ai

P.S. If your site scores under 60, I'll follow up personally with specific fixes."
            ;;
        med_spa)
            subject="Your $city med spa website — quick honest look"
            body="Hi ${biz_name} team,

Was browsing med spas in $city and wanted to give you something useful — an honest look at your website.

Med spa clients make their decision before they ever call you. They land on your site, spend about 8 seconds deciding if you look legit, and bounce or book. Most sites I see have 2-3 fixable issues that are silently killing conversions.

I built a free roast tool for exactly this:

👉 $ROAST_URL

No forms, no upsell — just a real scorecard on your site's headline, CTAs, trust signals, and mobile experience. Takes 60 seconds.

Rick
meetrick.ai"
            ;;
        roofing)
            subject="Your $city roofing site — what homeowners actually see"
            body="Hi ${biz_name} team,

Looked at your site while researching roofing companies in $city. Wanted to share something that might be useful.

When a homeowner has a leak or needs a new roof, they Google it, open 3-4 tabs, and pick the company that looks most trustworthy in the first 10 seconds. Most roofing sites lose that race on their homepage alone.

I built a free website roast tool — honest feedback, no agency fluff:

👉 $ROAST_URL

Paste your URL, get a real scorecard. Takes 60 seconds and might explain why some of your leads aren't calling back.

Rick
meetrick.ai"
            ;;
        physical_therapist)
            subject="Your $city PT website — what patients see first"
            body="Hi ${biz_name} team,

Found your practice while looking at physical therapists in $city — wanted to give you something actually useful.

Patients searching for PT in $city are comparing you against other practices in 10 seconds flat. A confusing headline, no clear booking path, or a slow mobile load can cost you real appointments every week.

Free roast tool — honest scorecard, no sales pitch:

👉 $ROAST_URL

Drop your URL and see exactly what's working and what isn't. 60 seconds.

Rick
meetrick.ai"
            ;;
        financial_advisor)
            subject="Your $city advisory website — what prospects notice"
            body="Hi ${biz_name} team,

Was looking at financial advisors in $city and wanted to pass something along.

When someone is ready to work with a financial advisor, they do their homework. Your website is usually their first impression — and most advisory sites I see either look dated, say nothing specific, or bury the trust signals that actually convert prospects.

I built a free roast tool for small business websites:

👉 $ROAST_URL

Honest scorecard — headline, CTA, mobile, trust signals. No signup, no pitch. 60 seconds.

Rick
meetrick.ai"
            ;;
        *)
            subject="Your $city business website — honest feedback"
            body="Hi ${biz_name} team,

Came across your website while looking at businesses in $city. Wanted to offer some honest feedback.

I built a free website roast tool — no sugarcoating, just a real scorecard on what's working and what might be costing you leads:

👉 $ROAST_URL

Takes 60 seconds. No signup required.

Rick
meetrick.ai"
            ;;
    esac
    
    # Send email
    local resend_id
    resend_id=$(send_email "$email" "$subject" "$body")
    
    if [ -n "$resend_id" ]; then
        echo "  ✅ Sent → $email (id: $resend_id)"
        log_pipeline "$biz_name" "$cat_key" "$city" "$state" "$email" "$domain" "$url" "$resend_id" "$confirmed"
        echo "$domain" >> "$SKIP_DOMAINS_FILE"
        SENT=$((SENT + 1))
    else
        echo "  ❌ Send failed for $email"
    fi
    
    sleep 0.4  # Rate limit buffer
}

# ── Target list ────────────────────────────────────────────────────────────────
declare -a SEARCHES=(
    "best dermatologist in Tampa FL website|Tampa|FL|dermatologist"
    "roofing company Tampa FL contact website|Tampa|FL|roofing"
    "best med spa in Memphis TN website|Memphis|TN|med_spa"
    "roofing contractor Memphis TN website|Memphis|TN|roofing"
    "dermatologist Albuquerque NM website contact|Albuquerque|NM|dermatologist"
    "med spa Louisville KY website|Louisville|KY|med_spa"
    "financial advisor Detroit MI small business website|Detroit|MI|financial_advisor"
    "dermatologist Cincinnati OH website|Cincinnati|OH|dermatologist"
    "physical therapist Sacramento CA website contact|Sacramento|CA|physical_therapist"
    "financial advisor Baltimore MD website|Baltimore|MD|financial_advisor"
    "dermatologist Columbus OH website contact|Columbus|OH|dermatologist"
    "med spa Cleveland OH website|Cleveland|OH|med_spa"
)

# ── Main loop ──────────────────────────────────────────────────────────────────
for search_entry in "${SEARCHES[@]}"; do
    if [ "$SENT" -ge "$TARGET" ]; then
        echo "🎯 Hit target of $TARGET sends"
        break
    fi
    
    IFS='|' read -r query city state cat_key <<< "$search_entry"
    echo ""
    echo "📍 $city, $state — $cat_key"
    
    # Get URLs from Bing
    mapfile -t urls < <(search_bing "$query")
    
    if [ ${#urls[@]} -eq 0 ]; then
        echo "  ⚠️  No URLs found for: $query"
        continue
    fi
    
    echo "  📌 Found ${#urls[@]} candidates"
    
    for url in "${urls[@]}"; do
        if [ "$SENT" -ge "$TARGET" ]; then break; fi
        process_business "$url" "$city" "$state" "$cat_key"
    done
done

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "✅ COMPLETE — $SENT emails sent this run"

# Log to daily note
TODAY=$(date +%Y-%m-%d)
DAILY_NOTE="$HOME/rick-vault/memory/$TODAY.md"
mkdir -p "$(dirname "$DAILY_NOTE")"
echo "" >> "$DAILY_NOTE"
echo "## Lead Blast Run — $(date +%H:%M)" >> "$DAILY_NOTE"
echo "- Sent: $SENT cold emails (target: $TARGET)" >> "$DAILY_NOTE"
echo "- Run: $(date)" >> "$DAILY_NOTE"

# Cleanup
rm -rf "$TMPDIR"
echo "Done."
