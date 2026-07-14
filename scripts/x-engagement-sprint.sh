#!/usr/bin/env bash
# x-engagement-sprint.sh
# Daily X engagement sprint: reply to all open mentions, post fresh content
# Runs autonomously — no human needed
#
# ⚠️ MIGRATION NOTE (2026-05-11): xpost/xurl OAuth tokens are REVOKED (401).
# This script's xpost calls are broken. Posting is now handled via agent-browser.
# See MEMORY.md: "✅ X VIA BROWSER" section for the live posting flow.
# For fresh original posts, the Content Machine cron (fad3e9aa) handles it via agent-browser.
# This script is kept for reference but xpost sections will fail — use agent-browser instead.

set -euo pipefail

WORKSPACE="$HOME/.openclaw/workspace"
VAULT="$HOME/rick-vault"
LOG="$VAULT/projects/x-twitter/engagement-sprint.log"
REPLIED_FILE="$VAULT/projects/x-twitter/replied-ids.json"
POSTS_LOG="$VAULT/projects/x-twitter/posts-log.json"

mkdir -p "$(dirname "$LOG")"
mkdir -p "$VAULT/projects/x-twitter"

echo "" >> "$LOG"
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) === ENGAGEMENT SPRINT START ===" >> "$LOG"

# Load replied IDs to avoid duplicates
if [[ ! -f "$REPLIED_FILE" ]]; then
  echo '{"replied": []}' > "$REPLIED_FILE"
fi
REPLIED_IDS=$(python3 -c "import json; d=json.load(open('$REPLIED_FILE')); print(' '.join(d['replied']))" 2>/dev/null || echo "")

# ── 1. REPLY TO ALL OPEN MENTIONS ──────────────────────────────────────────
echo "[mentions] Checking recent mentions..." >> "$LOG"

MENTIONS=$(xpost mentions --count 20 2>/dev/null) || { echo "[mentions] FAILED" >> "$LOG"; MENTIONS='{"data":[]}'; }

python3 << PYEOF
import json, subprocess, os, sys, codecs, re

log_path = "$LOG"
replied_path = "$REPLIED_FILE"
replied_ids_str = "$REPLIED_IDS"
replied_ids = set(replied_ids_str.split()) if replied_ids_str.strip() else set()

def log(msg):
    with open(log_path, 'a') as f:
        f.write(msg + "\n")
    print(msg)

def normalize_post_text(text):
    if not text:
        return ""
    text = text.strip()
    try:
        if re.search(r'\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8}', text):
            text = codecs.decode(text, 'unicode_escape')
    except Exception:
        pass
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace('“', '"').replace('”', '"')
    text = text.replace('‘', "'").replace('’', "'")
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

mentions_raw = '''$MENTIONS'''
try:
    data = json.loads(mentions_raw)
except:
    log("[mentions] Could not parse JSON")
    sys.exit(0)

tweets = data.get('data', [])
users = {u['id']: u for u in data.get('includes', {}).get('users', [])}

new_replied = list(replied_ids)

for t in tweets:
    tid = t['id']
    if tid in replied_ids:
        log(f"[mentions] Already replied to {tid}, skipping")
        continue

    author_id = t.get('author_id', '')
    user = users.get(author_id, {})
    username = user.get('username', 'unknown')
    text = t.get('text', '')

    # Skip our own tweets
    if author_id == '2032441385828380672':
        continue

    log(f"[mentions] Replying to @{username}: {text[:80]}...")

    # Generate a sharp contextual reply using the tweet content
    # Keep replies short, genuine, conversation-driving
    reply_prompt = f"""You are Rick, an autonomous AI CEO (@MeetRickAI). Reply to this tweet from @{username}:

\"{text}\"

Rules:
- 1-3 sentences max
- Sharp, warm, specific to what they said
- End with a question OR a provocative statement that invites a response
- No hashtags
- No emojis unless it feels natural
- Sound human, not robotic
- Don't mention you're an AI unless directly relevant
- Don't start with \"Great point\" or sycophantic openers

Reply only with the tweet text, nothing else."""

    try:
        result = subprocess.run(
            ['claude', '-p', reply_prompt, '--model', 'claude-sonnet-4-6'],
            capture_output=True, text=True, timeout=30
        )
        reply_text = normalize_post_text(result.stdout)
        if not reply_text:
            log(f"[mentions] Empty reply generated for {tid}, skipping")
            continue
        if '\\u' in reply_text:
            log(f"[mentions] Literal unicode escape remained for {tid}, skipping: {reply_text[:120]}")
            continue

        # Post the reply
        post_result = subprocess.run(
            ['xpost', 'reply', tid, f'@{username} {reply_text}'],
            capture_output=True, text=True, timeout=15
        )
        resp = json.loads(post_result.stdout)
        if 'data' in resp:
            log(f"[mentions] Replied to @{username} (tweet {tid}) ✅")
            new_replied.append(tid)
        else:
            log(f"[mentions] Reply failed for {tid}: {post_result.stdout[:200]}")
    except Exception as e:
        log(f"[mentions] Error replying to {tid}: {e}")

# Save updated replied IDs (keep last 500)
with open(replied_path, 'w') as f:
    json.dump({"replied": new_replied[-500:]}, f)

log(f"[mentions] Done. Total replied-to IDs tracked: {len(new_replied)}")
PYEOF

echo "[mentions] Reply loop complete" >> "$LOG"

# ── 2. FRESH ORIGINAL POSTS ────────────────────────────────────────────────
echo "[posts] Generating fresh content..." >> "$LOG"

# Get recent posts to avoid repetition
RECENT=$(xpost timeline MeetRickAI --count 10 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
texts = [t['text'][:80] for t in d.get('data', [])]
print('\n'.join(texts))
" 2>/dev/null || echo "")

# Generate 1 original post per sprint (2x/day = 2 posts/day, high quality)
POST_PROMPT="You are Rick (@MeetRickAI), an autonomous AI CEO built by a solo founder to reach \$100K MRR. You post on X/Twitter to grow an audience of founders, indie hackers, and builders.

Recent posts (don't repeat these angles):
$RECENT

Write ONE high-quality tweet. Rotate between these formats:
1. Raw honest number/stat with context (e.g. 'Week 2: X happened, Y didn't')
2. Contrarian hot take about AI, startups, or solopreneurship
3. Behind-the-scenes of what an AI CEO actually does (specific, not vague)
4. Question that founders genuinely want to answer

Rules:
- Max 280 chars
- No hashtags
- Conversational, not corporate
- Real > polished
- Include meetrick.ai only if it fits naturally (not every post)
- If using a list format, max 4 items
- End on a hook or open loop when possible

Output only the tweet text, nothing else."

POST_TEXT=$(claude -p "$POST_PROMPT" --model claude-sonnet-4-6 2>/dev/null | python3 -c '
import sys, re, codecs
text = sys.stdin.read().strip()
try:
    if re.search(r"\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8}", text):
        text = codecs.decode(text, "unicode_escape")
except Exception:
    pass
text = text.replace("\r\n", "\n").replace("\r", "\n")
text = text.replace("“", "\"").replace("”", "\"").replace("‘", "'").replace("’", "'")
text = re.sub(r"\n{3,}", "\n\n", text).strip()
if "\\u" in text:
    sys.exit(2)
sys.stdout.write(text[:270])
') || POST_TEXT=""

if [[ -n "$POST_TEXT" ]]; then
  POST_RESULT=$(xpost post "$POST_TEXT" 2>/dev/null) || POST_RESULT='{"error":"failed"}'
  POST_ID=$(echo "$POST_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('id',''))" 2>/dev/null || echo "")
  if [[ -n "$POST_ID" ]]; then
    echo "[posts] Posted tweet $POST_ID ✅" >> "$LOG"
    echo "[posts] Content: $POST_TEXT" >> "$LOG"
    # Log to posts log
    python3 << PYEOF2
import json, os
from datetime import datetime, timezone

posts_log = "$POSTS_LOG"
new_entry = {
    "id": "$POST_ID",
    "text": """$POST_TEXT""",
    "posted_at": datetime.now(timezone.utc).isoformat(),
    "likes": 0,
    "impressions": 0,
    "replies": 0
}
existing = []
if os.path.exists(posts_log):
    try:
        existing = json.load(open(posts_log))
    except:
        pass
existing.append(new_entry)
json.dump(existing[-200:], open(posts_log, 'w'), indent=2)
PYEOF2
  else
    echo "[posts] Post failed: $POST_RESULT" >> "$LOG"
  fi
else
  echo "[posts] Content generation failed" >> "$LOG"
fi

# ── 3. LIKE ENGAGED REPLIES ────────────────────────────────────────────────
echo "[likes] Liking quality replies to our tweets..." >> "$LOG"

python3 << PYEOF3
import json, subprocess

log_path = "$LOG"

def log(msg):
    with open(log_path, 'a') as f:
        f.write(msg + "\n")

mentions_raw = '''$MENTIONS'''
try:
    data = json.loads(mentions_raw)
except:
    sys.exit(0)

tweets = data.get('data', [])
liked = 0
for t in tweets:
    m = t.get('public_metrics', {})
    # Like replies that have substance (>0 impressions, not our own)
    if t.get('author_id') != '2032441385828380672' and m.get('impression_count', 0) > 0:
        try:
            result = subprocess.run(['xpost', 'like', t['id']], capture_output=True, text=True, timeout=10)
            resp = json.loads(result.stdout)
            if resp.get('data', {}).get('liked'):
                liked += 1
        except:
            pass

log(f"[likes] Liked {liked} replies ✅")
PYEOF3

echo "=== SPRINT COMPLETE ===" >> "$LOG"
echo "" >> "$LOG"
