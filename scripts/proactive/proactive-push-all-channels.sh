#!/usr/bin/env bash
set -euo pipefail
# proactive-push-all-channels.sh — Post to ALL channels with gap detection + fallback content
# Called by OpenClaw cron at 2am and 4am PT (overnight push windows)
#
# Channels: X (xpost), LinkedIn (CDP 9225), Reddit (CDP 9223), 
#           Instagram/Threads (CDP 9222), Moltbook (API)
#
# Usage:
#   proactive-push-all-channels.sh              # normal run
#   proactive-push-all-channels.sh --dry-run    # log what would happen
#   proactive-push-all-channels.sh --channel x  # single channel only

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
LOG_FILE="$DATA_ROOT/logs/proactive-push.log"
STATE_FILE="$DATA_ROOT/brain/push-state.json"
MIN_GAP_HOURS=6
DRY_RUN=false
SINGLE_CHANNEL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --channel) SINGLE_CHANNEL="$2"; shift 2 ;;
        *) shift ;;
    esac
done

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$STATE_FILE")"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg" | tee -a "$LOG_FILE"
}

# Initialize state if missing
if [[ ! -f "$STATE_FILE" ]]; then
    cat > "$STATE_FILE" <<'EOF'
{
  "last_push": {
    "x": "1970-01-01T00:00:00",
    "linkedin": "1970-01-01T00:00:00",
    "reddit": "1970-01-01T00:00:00",
    "instagram": "1970-01-01T00:00:00",
    "threads": "1970-01-01T00:00:00",
    "moltbook": "1970-01-01T00:00:00"
  },
  "push_count": {
    "x": 0, "linkedin": 0, "reddit": 0, "instagram": 0, "threads": 0, "moltbook": 0
  }
}
EOF
fi

# Read state
get_last_push() {
    local channel="$1"
    python3 -c "
import json, sys
with open('$STATE_FILE') as f:
    d = json.load(f)
print(d.get('last_push',{}).get('$channel','1970-01-01T00:00:00'))
"
}

update_push_state() {
    local channel="$1"
    python3 -c "
import json
from datetime import datetime
with open('$STATE_FILE') as f:
    d = json.load(f)
d['last_push']['$channel'] = datetime.now().isoformat()
d['push_count']['$channel'] = d.get('push_count',{}).get('$channel',0) + 1
with open('$STATE_FILE','w') as f:
    json.dump(d, f, indent=2)
"
}

# Check if gap > MIN_GAP_HOURS
should_push() {
    local channel="$1"
    local last=$(get_last_push "$channel")
    python3 -c "
from datetime import datetime, timedelta
last = datetime.fromisoformat('$last'.replace('Z','+00:00').replace('+00:00',''))
gap = (datetime.now() - last).total_seconds() / 3600
if gap >= $MIN_GAP_HOURS:
    print('yes')
else:
    print('no')
"
}

# Get live proof data for content
get_proof_data() {
    python3 -c "
import json, os, re
from pathlib import Path
data_root = Path(os.getenv('RICK_DATA_ROOT', str(Path.home() / 'rick-vault')))
proof = {}

# Truth source #1: latest reconciliation-*.md (hand-curated, filters phantom subs)
recs = sorted((data_root / 'revenue').glob('reconciliation-*.md'), reverse=True)
if recs:
    text = recs[0].read_text(encoding='utf-8', errors='replace')
    m = re.search(r'Real\s+current\s+MRR[^\$]*\$\s*([0-9]+(?:\.[0-9]+)?)', text, re.IGNORECASE)
    if m:
        try: proof['mrr'] = float(m.group(1))
        except: pass

# Truth source #2: brain/state.json (only if reconciliation missing)
if not proof.get('mrr'):
    state = data_root / 'brain/state.json'
    if state.exists():
        try:
            s = json.loads(state.read_text())
            v = s.get('revenue',{}).get('mrr', 0)
            # Reject the known phantom \$547 — never emit it.
            if v and v != 547:
                proof['mrr'] = v
        except: pass

# Graceful fallback: 0, NOT \$547
if not proof.get('mrr'):
    proof['mrr'] = 0
proof['site'] = 'meetrick.ai'
proof['product'] = 'AI CEO — manages your business 24/7'
print(json.dumps(proof))
"
}

# Generate content using OpenAI API (fast + reliable)
generate_content() {
    local channel="$1"
    local proof="$2"
    local mrr=$(echo "$proof" | python3 -c "import json,sys; print(json.load(sys.stdin).get('mrr',0))")
    
    python3 -c "
import json, os, urllib.request

channel = '$channel'
mrr = '$mrr'
api_key = os.getenv('OPENAI_API_KEY', '')

if not api_key:
    # Fallback content
    print(f'Building in public as an AI CEO. Current MRR: \${mrr}. Ship daily, measure everything, let the data decide. meetrick.ai')
    exit()

prompt = f'''Write a single {channel} post for Rick, AI CEO of meetrick.ai.
Current MRR: \${mrr}. Product: AI CEO that runs your business autonomously.
Rules:
- Channel: {channel}
- Be conversational, not corporate
- Include a real proof point (the MRR, or that Rick is an AI running a real business)
- Max 250 chars for X/Threads, 500 for LinkedIn/Reddit/Moltbook
- No hashtags
- End with meetrick.ai if it fits naturally
- Sound like a founder sharing a genuine observation, not marketing
Return ONLY the post text, nothing else.'''

try:
    payload = json.dumps({
        'model': 'gpt-4o-mini',
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.8,
        'max_tokens': 300,
    })
    req = urllib.request.Request(
        'https://api.openai.com/v1/chat/completions',
        data=payload.encode(),
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
        text = data['choices'][0]['message']['content'].strip()
        # Remove quotes if wrapped
        if text.startswith('\"') and text.endswith('\"'):
            text = text[1:-1]
        print(text)
except Exception as e:
    print(f'Building in public as an AI CEO. Current MRR: \${mrr}. Ship daily, measure everything. meetrick.ai')
" 2>/dev/null
}

# Channel-specific push functions
push_x() {
    local content="$1"
    if [[ "$DRY_RUN" == "true" ]]; then
        log "[DRY-RUN] X: Would post: ${content:0:100}..."
        return 0
    fi
    local result
    result=$(xpost post "$content" 2>&1) || { log "[ERROR] X post failed: $result"; return 1; }
    local post_id=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('data',{}).get('id','unknown'))" 2>/dev/null || echo "unknown")
    log "[OK] X: Posted (id: $post_id)"
    update_push_state "x"
}

push_moltbook() {
    local content="$1"
    local api_key="${MOLTBOOK_API_KEY:-}"
    if [[ -z "$api_key" ]]; then
        # Try reading from credentials file
        api_key=$(python3 -c "import json; print(json.load(open('$HOME/.config/moltbook/credentials.json'))['api_key'])" 2>/dev/null || echo "")
    fi
    if [[ -z "$api_key" ]]; then
        log "[SKIP] Moltbook: No API key"
        return 1
    fi
    if [[ "$DRY_RUN" == "true" ]]; then
        log "[DRY-RUN] Moltbook: Would post: ${content:0:100}..."
        return 0
    fi
    # Generate a short title from content
    local title=$(echo "$content" | head -c 80 | sed 's/\(.*[.!?]\).*/\1/' | head -c 80)
    [[ -z "$title" ]] && title="Building in public"
    local result
    result=$(curl -s -X POST "https://www.moltbook.com/api/v1/posts" \
        -H "Authorization: Bearer $api_key" \
        -H "Content-Type: application/json" \
        -d "$(jq -n --arg c "$content" --arg t "$title" '{content: $c, title: $t, submolt_name: "general", submolt: "general"}')" 2>&1) || { log "[ERROR] Moltbook: $result"; return 1; }
    # Check for rate limit (2.5 min between posts) — Moltbook returns a plain message, not 429
    if echo "$result" | grep -qi "only post once"; then
        log "[SKIP] Moltbook: Rate limited (2.5 min cooldown)"
        return 0
    fi
    local post_id=$(echo "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('post',{}).get('id',d.get('id',d.get('post_id','unknown'))))" 2>/dev/null || echo "posted")
    if [[ "$post_id" == "unknown" ]] || echo "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if d.get('success') else 1)" 2>/dev/null; then
        log "[OK] Moltbook: Posted (id: $post_id)"
        update_push_state "moltbook"
    else
        log "[ERROR] Moltbook: $result"
        return 1
    fi
}

push_linkedin() {
    local content="$1"
    if ! curl -s http://localhost:9225/json/version >/dev/null 2>&1; then
        log "[SKIP] LinkedIn: CDP port 9225 not available"
        return 1
    fi
    if [[ "$DRY_RUN" == "true" ]]; then
        log "[DRY-RUN] LinkedIn: Would post via CDP: ${content:0:100}..."
        return 0
    fi
    # Use playwright to post via CDP
    node "$SCRIPT_DIR/cdp-post.mjs" --port 9225 --platform linkedin --text "$content" 2>&1 | while read -r line; do log "[LinkedIn CDP] $line"; done
    local exit_code=${PIPESTATUS[0]}
    if [[ $exit_code -eq 0 ]]; then
        log "[OK] LinkedIn: Posted via CDP"
        update_push_state "linkedin"
    else
        log "[ERROR] LinkedIn: CDP post failed (exit $exit_code)"
        return 1
    fi
}

push_reddit() {
    local content="$1"
    if ! curl -s http://localhost:9223/json/version >/dev/null 2>&1; then
        log "[SKIP] Reddit: CDP port 9223 not available"
        return 1
    fi
    if [[ "$DRY_RUN" == "true" ]]; then
        log "[DRY-RUN] Reddit: Would post via CDP: ${content:0:100}..."
        return 0
    fi
    # Reddit engagement is better as replies to existing threads, not self-posts
    # This will be handled by the dedicated Reddit cron; log skip here
    log "[INFO] Reddit: Delegated to dedicated engagement cron (reply-based strategy)"
    update_push_state "reddit"
}

push_threads() {
    local content="$1"
    if ! curl -s http://localhost:9222/json/version >/dev/null 2>&1; then
        log "[SKIP] Threads: CDP port 9222 not available"
        return 1
    fi
    if [[ "$DRY_RUN" == "true" ]]; then
        log "[DRY-RUN] Threads: Would post via CDP: ${content:0:100}..."
        return 0
    fi
    node "$SCRIPT_DIR/cdp-post.mjs" --port 9222 --platform threads --text "$content" 2>&1 | while read -r line; do log "[Threads CDP] $line"; done
    local exit_code=${PIPESTATUS[0]}
    if [[ $exit_code -eq 0 ]]; then
        log "[OK] Threads: Posted via CDP"
        update_push_state "threads"
    else
        log "[ERROR] Threads: CDP post failed (exit $exit_code)"
        return 1
    fi
}

push_instagram() {
    local content="$1"
    if ! curl -s http://localhost:9222/json/version >/dev/null 2>&1; then
        log "[SKIP] Instagram: CDP port 9222 not available"
        return 1
    fi
    if [[ "$DRY_RUN" == "true" ]]; then
        log "[DRY-RUN] Instagram: Would post via CDP: ${content:0:100}..."
        return 0
    fi
    # Instagram requires images — skip in automated push, handle via dedicated cron
    log "[INFO] Instagram: Requires image content — delegated to dedicated engagement cron"
    update_push_state "instagram"
}

# Main execution
log "========== Proactive Push Run Started =========="
PROOF=$(get_proof_data)
CHANNELS=("x" "linkedin" "threads" "moltbook")
# Reddit and Instagram are reply/image-based — handled by dedicated crons
SUCCESS=0
FAIL=0
SKIPPED=0

for channel in "${CHANNELS[@]}"; do
    if [[ -n "$SINGLE_CHANNEL" && "$channel" != "$SINGLE_CHANNEL" ]]; then
        continue
    fi
    
    if [[ "$(should_push "$channel")" == "no" ]]; then
        log "[SKIP] $channel: Last push was < ${MIN_GAP_HOURS}h ago"
        ((SKIPPED++)) || true
        continue
    fi
    
    CONTENT=$(generate_content "$channel" "$PROOF")
    if [[ -z "$CONTENT" ]]; then
        log "[ERROR] $channel: Content generation failed"
        ((FAIL++)) || true
        continue
    fi
    
    case "$channel" in
        x) push_x "$CONTENT" && ((SUCCESS++)) || ((FAIL++)) ;;
        linkedin) push_linkedin "$CONTENT" && ((SUCCESS++)) || ((FAIL++)) ;;
        reddit) push_reddit "$CONTENT" && ((SUCCESS++)) || ((FAIL++)) ;;
        threads) push_threads "$CONTENT" && ((SUCCESS++)) || ((FAIL++)) ;;
        instagram) push_instagram "$CONTENT" && ((SUCCESS++)) || ((FAIL++)) ;;
        moltbook) push_moltbook "$CONTENT" && ((SUCCESS++)) || ((FAIL++)) ;;
    esac
done

log "========== Push Complete: $SUCCESS ok, $FAIL fail, $SKIPPED skip =========="
