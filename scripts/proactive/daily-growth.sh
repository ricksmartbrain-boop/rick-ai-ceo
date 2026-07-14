#!/usr/bin/env bash
set -euo pipefail
# daily-growth.sh — Systematic follower/reach growth across all channels
# Run daily at 10am PT
#
# Strategy:
# 1. X: Follow relevant accounts, engage with high-follower posts
# 2. Reddit: Reply to top posts with genuine value (via CDP)
# 3. LinkedIn: Connection requests to founders (via CDP)
# 4. Moltbook: Comment on trending posts (via API)
# 5. Track all growth metrics

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
LOG_FILE="$DATA_ROOT/logs/daily-growth.log"
STATE_FILE="$DATA_ROOT/brain/state.json"
GROWTH_FILE="$DATA_ROOT/brain/growth-metrics.json"
X_KEYS="$HOME/.config/x-api/keys.env"

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$GROWTH_FILE")"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg" | tee -a "$LOG_FILE"
}

# Load X API keys
load_x_keys() {
    if [[ -f "$X_KEYS" ]]; then
        source "$X_KEYS"
    fi
}

# Initialize growth metrics if missing
if [[ ! -f "$GROWTH_FILE" ]]; then
    cat > "$GROWTH_FILE" <<'EOF'
{
  "daily_snapshots": [],
  "targets": {
    "x_followers": 100,
    "linkedin_connections": 50,
    "moltbook_followers": 20,
    "reddit_karma": 50
  }
}
EOF
fi

# ── X Growth ──────────────────────────────────────────────────────────────────
grow_x() {
    log "=== X Growth ==="
    load_x_keys
    
    # 1. Get current follower count
    local me_data
    me_data=$(xpost me --json 2>/dev/null || echo '{}')
    local followers=$(echo "$me_data" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('public_metrics',{}).get('followers_count',0))" 2>/dev/null || echo "0")
    log "Current X followers: $followers"
    
    # 2. Search for relevant conversations to engage with
    local searches=("AI CEO" "AI automation SaaS" "building in public AI" "AI agent startup")
    local engaged=0
    
    for query in "${searches[@]}"; do
        local results
        results=$(xpost search "$query" --count 5 --json 2>/dev/null || echo '{"data":[]}')
        
        # Like and engage with relevant tweets
        local tweet_ids
        tweet_ids=$(echo "$results" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for t in data.get('data', [])[:3]:
        print(t['id'])
except: pass
" 2>/dev/null)
        
        while IFS= read -r tweet_id; do
            if [[ -n "$tweet_id" ]]; then
                xpost like "$tweet_id" 2>/dev/null && log "[X] Liked tweet $tweet_id" || true
                ((engaged++)) || true
                sleep 2  # rate limit protection
            fi
        done <<< "$tweet_ids"
        
        if [[ $engaged -ge 10 ]]; then
            break  # don't over-engage
        fi
    done
    
    log "[X] Engaged with $engaged tweets"
    
    # 3. Follow relevant accounts (founders, AI builders)
    # Search for accounts that tweet about SaaS + AI
    local follow_targets
    follow_targets=$(xpost search "AI SaaS founder" --count 10 --json 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    seen = set()
    for t in data.get('data', []):
        uid = t.get('author_id', '')
        if uid and uid not in seen:
            seen.add(uid)
            print(uid)
except: pass
" 2>/dev/null | head -5)
    
    local followed=0
    while IFS= read -r user_id; do
        if [[ -n "$user_id" && "$user_id" != "$X_USER_ID" ]]; then
            # Follow via X API v2 with OAuth1 (user context required)
            local follow_result
            follow_result=$(python3 -c "
import json, os, urllib.request, hmac, hashlib, base64, time, uuid

api_key = os.environ.get('X_API_KEY','')
api_secret = os.environ.get('X_API_SECRET','')
access_token = os.environ.get('X_ACCESS_TOKEN','')
access_secret = os.environ.get('X_ACCESS_TOKEN_SECRET','')
user_id = os.environ.get('X_USER_ID','')

url = f'https://api.twitter.com/2/users/{user_id}/following'
body = json.dumps({'target_user_id': '$user_id'})

# OAuth1 signature
nonce = uuid.uuid4().hex
ts = str(int(time.time()))
params = {
    'oauth_consumer_key': api_key,
    'oauth_nonce': nonce,
    'oauth_signature_method': 'HMAC-SHA256',
    'oauth_timestamp': ts,
    'oauth_token': access_token,
    'oauth_version': '1.0',
}
param_str = '&'.join(f'{k}={urllib.request.quote(v,safe=\"\")}' for k,v in sorted(params.items()))
base = f'POST&{urllib.request.quote(url,safe=\"\")}&{urllib.request.quote(param_str,safe=\"\")}'
key = f'{urllib.request.quote(api_secret,safe=\"\")}&{urllib.request.quote(access_secret,safe=\"\")}'
sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()).decode()
params['oauth_signature'] = sig
auth_header = 'OAuth ' + ', '.join(f'{k}=\"{urllib.request.quote(v,safe=\"\")}\"' for k,v in sorted(params.items()))

req = urllib.request.Request(url, data=body.encode(), headers={
    'Authorization': auth_header,
    'Content-Type': 'application/json',
})
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
        print(json.dumps(data))
except urllib.error.HTTPError as e:
    print(json.dumps({'error': str(e), 'status': e.code}))
except Exception as e:
    print(json.dumps({'error': str(e)}))
" 2>/dev/null || echo '{}')
            log "[X] Follow attempt: $user_id -> $(echo "$follow_result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('following', d.get('error','unknown')))" 2>/dev/null || echo 'unknown')"
            ((followed++)) || true
            sleep 3
        fi
    done <<< "$follow_targets"
    
    log "[X] Followed $followed accounts"
    echo "$followers"  # return follower count
}

# ── Moltbook Growth ──────────────────────────────────────────────────────────
grow_moltbook() {
    log "=== Moltbook Growth ==="
    local api_key
    api_key=$(python3 -c "import json; print(json.load(open('$HOME/.config/moltbook/credentials.json'))['api_key'])" 2>/dev/null || echo "")
    
    if [[ -z "$api_key" ]]; then
        log "[SKIP] Moltbook: No API key"
        return
    fi
    
    # Get feed and engage with recent posts
    local feed
    feed=$(curl -s "https://www.moltbook.com/api/v1/feed?limit=10" \
        -H "X-API-Key: $api_key" 2>/dev/null || echo '[]')
    
    local commented=0
    local post_ids
    post_ids=$(echo "$feed" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    posts = data if isinstance(data, list) else data.get('posts', data.get('data', []))
    for p in posts[:5]:
        pid = p.get('id', p.get('post_id', ''))
        if pid:
            print(pid)
except: pass
" 2>/dev/null)
    
    while IFS= read -r post_id; do
        if [[ -n "$post_id" ]]; then
            # Comment on the post
            local comment="Great insight! Building meetrick.ai taught me something similar — the compounding effect of daily iteration is underrated."
            curl -s -X POST "https://www.moltbook.com/api/v1/posts/$post_id/comments" \
                -H "X-API-Key: $api_key" \
                -H "Content-Type: application/json" \
                -d "$(jq -n --arg c "$comment" '{content: $c}')" >/dev/null 2>&1 || true
            ((commented++)) || true
            sleep 2
        fi
    done <<< "$post_ids"
    
    log "[Moltbook] Commented on $commented posts"
}

# ── Track Growth Metrics ─────────────────────────────────────────────────────
track_metrics() {
    local x_followers="${1:-0}"
    
    python3 -c "
import json
from datetime import date
from pathlib import Path

gf = Path('$GROWTH_FILE')
data = json.loads(gf.read_text())

today = date.today().isoformat()
snapshot = {
    'date': today,
    'x_followers': int('$x_followers' or 0),
    'actions': {
        'x_likes': 0,
        'x_follows': 0,
        'moltbook_comments': 0,
        'linkedin_connections': 0,
        'reddit_replies': 0
    }
}

# Remove existing today entry
data['daily_snapshots'] = [s for s in data['daily_snapshots'] if s['date'] != today]
data['daily_snapshots'].append(snapshot)
# Keep last 90 days
data['daily_snapshots'] = data['daily_snapshots'][-90:]

gf.write_text(json.dumps(data, indent=2))
print(f'Growth metrics updated for {today}')
"
}

# ── Main ─────────────────────────────────────────────────────────────────────
log "========== Daily Growth Run Started =========="

X_FOLLOWERS=$(grow_x 2>/dev/null | tail -1)
grow_moltbook

# LinkedIn and Reddit growth handled by their dedicated OpenClaw crons
# which already run via CDP. We just need to ensure they're active.
log "[INFO] LinkedIn growth: handled by cron 6fcabd5c (LinkedIn daily post — 8am PT)"
log "[INFO] Reddit growth: handled by cron 547e667b (Reddit engagement — daily 10am PT)"

track_metrics "$X_FOLLOWERS"
log "========== Daily Growth Complete =========="
