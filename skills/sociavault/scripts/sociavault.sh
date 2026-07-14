#!/usr/bin/env bash
# SociaVault CLI wrapper for Rick
# Usage: bash sociavault.sh <command> <arg>

set -euo pipefail

# Load API key
source ~/clawd/config/rick.env 2>/dev/null || true
API_KEY="${SOCIAVAULT_API_KEY:-}"
BASE="https://api.sociavault.com/v1"

if [ -z "$API_KEY" ]; then
  echo "ERROR: SOCIAVAULT_API_KEY not set" >&2
  exit 1
fi

call() {
  curl -s "$BASE/$1" -H "X-API-Key: $API_KEY"
}

cmd="${1:-help}"
arg="${2:-}"

case "$cmd" in
  credits)
    call "credits" | python3 -m json.tool
    ;;
  enrich-x)
    [ -z "$arg" ] && { echo "Usage: sociavault.sh enrich-x <handle>"; exit 1; }
    call "scrape/twitter/profile?handle=$arg" | python3 -c "
import json,sys
d=json.load(sys.stdin).get('data',{})
legacy=d.get('legacy',{})
print(f\"Handle: @{legacy.get('screen_name','?')}\")
print(f\"Name: {legacy.get('name','?')}\")
print(f\"Bio: {legacy.get('description','?')[:120]}\")
print(f\"Followers: {legacy.get('followers_count','?')}\")
print(f\"Following: {legacy.get('friends_count','?')}\")
print(f\"Tweets: {legacy.get('statuses_count','?')}\")
print(f\"Verified: {d.get('is_blue_verified',False)}\")
print(f\"Created: {legacy.get('created_at','?')}\")
" 2>/dev/null
    ;;
  enrich-linkedin)
    [ -z "$arg" ] && { echo "Usage: sociavault.sh enrich-linkedin <linkedin_url>"; exit 1; }
    call "scrape/linkedin/profile?url=$arg" | python3 -c "
import json,sys
d=json.load(sys.stdin).get('data',{})
print(f\"Name: {d.get('name','?')}\")
print(f\"Location: {d.get('location','?')}\")
print(f\"Followers: {d.get('followers','?')}\")
print(f\"Connections: {d.get('connections','?')}\")
print(f\"About: {str(d.get('about',''))[:200]}\")
exp=d.get('experience',[])
if exp:
    e=exp[0] if isinstance(exp,list) else {}
    print(f\"Current Role: {e.get('title','?')} at {e.get('company','?')}\")
" 2>/dev/null
    ;;
  enrich-ig)
    [ -z "$arg" ] && { echo "Usage: sociavault.sh enrich-ig <handle>"; exit 1; }
    call "scrape/instagram/profile?handle=$arg" | python3 -m json.tool 2>/dev/null | head -30
    ;;
  buyer-intent)
    [ -z "$arg" ] && { echo "Usage: sociavault.sh buyer-intent <query>"; exit 1; }
    encoded=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$arg'))")
    echo "=== Reddit ==="
    call "scrape/reddit/search?query=$encoded&limit=5" | python3 -c "
import json,sys
d=json.load(sys.stdin)
posts=d.get('data',{}).get('posts',d.get('data',[]))
if isinstance(posts,list):
    for p in posts[:5]:
        print(f\"- {p.get('title','')[:100]}\")
        print(f\"  r/{p.get('subreddit','')} | {p.get('score',0)} pts | {p.get('num_comments',0)} comments\")
" 2>/dev/null
    echo ""
    echo "=== Threads ==="
    call "scrape/threads/search?query=$encoded&limit=5" | python3 -c "
import json,sys
d=json.load(sys.stdin)
posts=d.get('data',[])
if isinstance(posts,list):
    for p in posts[:5]:
        text=p.get('text',p.get('caption',''))[:100]
        print(f\"- {text}\")
" 2>/dev/null
    ;;
  competitor-ads)
    [ -z "$arg" ] && { echo "Usage: sociavault.sh competitor-ads <company>"; exit 1; }
    encoded=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$arg'))")
    echo "=== Facebook Ad Library ==="
    call "scrape/facebook-ad-library/search?query=$encoded&limit=5" | python3 -c "
import json,sys
d=json.load(sys.stdin)
ads=d.get('data',[])
if isinstance(ads,list):
    for a in ads[:5]:
        print(f\"- {a.get('ad_creative_bodies',[''])[0][:120] if isinstance(a.get('ad_creative_bodies'),list) else str(a)[:120]}\")
" 2>/dev/null
    ;;
  search-google)
    [ -z "$arg" ] && { echo "Usage: sociavault.sh search-google <query>"; exit 1; }
    encoded=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$arg'))")
    call "scrape/google/search?query=$encoded" | python3 -m json.tool 2>/dev/null | head -50
    ;;
  help|*)
    echo "SociaVault CLI — Social Media Data API"
    echo ""
    echo "Commands:"
    echo "  credits              Check credit balance"
    echo "  enrich-x <handle>    Get X/Twitter profile"
    echo "  enrich-linkedin <url> Get LinkedIn profile"
    echo "  enrich-ig <handle>   Get Instagram profile"
    echo "  buyer-intent <query> Search Reddit+Threads for buyer signals"
    echo "  competitor-ads <co>  Search Facebook Ad Library"
    echo "  search-google <q>    Google search"
    echo ""
    echo "Credits: 1 per call (most endpoints)"
    ;;
esac
