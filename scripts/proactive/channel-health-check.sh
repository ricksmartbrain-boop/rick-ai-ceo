#!/usr/bin/env bash
set -euo pipefail
# channel-health-check.sh — Verify all channels are active and posting
# Run every 6 hours. If any channel has gone >12h without a post, alert.

# Source env FIRST so RICK_DATA_ROOT is available before DATA_ROOT is captured.
source "$HOME/.openclaw/workspace/config/rick.env" 2>/dev/null || true

# Push-state and formatter logs live in rick-vault (maintained by proactive-push scripts).
# Use rick-vault as the canonical state store regardless of RICK_DATA_ROOT.
DATA_ROOT="$HOME/rick-vault"
STATE_FILE="$DATA_ROOT/brain/push-state.json"
LOG_FILE="$DATA_ROOT/logs/channel-health.log"
DEFAULT_ALERT_THRESHOLD_HOURS=12
# Channel-specific thresholds prevent false positives for surfaces that post on a daily cadence.
# Keep X tighter because it is expected to post multiple times per day.
# Instagram is capped at 2/day so 24h prevents false positives from the daily cap.
CHANNEL_THRESHOLDS_JSON='{"x": 12, "moltbook": 24, "instagram": 24, "threads": 24}'
FORMATTER_LOG_DIR="$DATA_ROOT/operations"
# Runtime data root (rick-install-test) — may differ from proactive-push DATA_ROOT
RUNTIME_DATA_ROOT="${RICK_DATA_ROOT:-$DATA_ROOT}"

if [[ -z "${MOLTBOOK_API_KEY:-}" && -f "$HOME/.config/moltbook/credentials.json" ]]; then
    MOLTBOOK_API_KEY=$(python3 - <<'PY'
import json
from pathlib import Path
p = Path.home() / '.config' / 'moltbook' / 'credentials.json'
try:
    data = json.loads(p.read_text())
    print(data.get('api_key', ''))
except Exception:
    print('')
PY
)
fi

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

if [[ ! -f "$STATE_FILE" ]]; then
    log "[WARN] No push state file — channels have never posted"
    exit 0
fi

sync_x_state_from_live() {
    local latest_created_at="$1"
    [[ -z "$latest_created_at" ]] && return 0

    STATE_FILE="$STATE_FILE" LATEST_CREATED_AT="$latest_created_at" python3 - <<'PY'
import json, os
from datetime import datetime
from pathlib import Path

state_file = Path(os.path.expanduser(os.environ['STATE_FILE']))
live_raw = os.environ['LATEST_CREATED_AT'].strip()
if not live_raw:
    raise SystemExit(0)

live_dt = datetime.fromisoformat(live_raw.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
state = json.loads(state_file.read_text())
last_raw = state.get('last_push', {}).get('x', '1970-01-01T00:00:00')
last_dt = datetime.fromisoformat(last_raw.replace('Z', '+00:00').replace('+00:00', ''))

if live_dt > last_dt:
    state.setdefault('last_push', {})['x'] = live_dt.isoformat(timespec='seconds')
    state_file.write_text(json.dumps(state, indent=2))
    print(f'ℹ️ x: push-state synced from live timeline ({live_dt.isoformat(timespec="seconds")})')
PY
}

sync_state_from_formatter_log() {
    local channel="$1"
    local log_file="$2"
    [[ -f "$log_file" ]] || return 0

    STATE_FILE="$STATE_FILE" CHANNEL="$channel" LOG_FILE="$log_file" python3 - <<'PY'
import json, os
from datetime import datetime
from pathlib import Path

state_file = Path(os.path.expanduser(os.environ['STATE_FILE']))
channel = os.environ['CHANNEL']
log_file = Path(os.path.expanduser(os.environ['LOG_FILE']))

latest = None
for line in log_file.read_text().splitlines():
    line = line.strip()
    if not line or not line.startswith('{'):
        continue
    try:
        entry = json.loads(line)
    except Exception:
        continue
    if entry.get('live') is not True:
        continue
    ran_at = entry.get('ran_at')
    if not ran_at:
        continue
    try:
        dt = datetime.fromisoformat(ran_at.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
    except Exception:
        continue
    if latest is None or dt > latest:
        latest = dt

if latest is None:
    raise SystemExit(0)

state = json.loads(state_file.read_text())
last_raw = state.get('last_push', {}).get(channel, '1970-01-01T00:00:00')
last_dt = datetime.fromisoformat(last_raw.replace('Z', '+00:00').replace('+00:00', ''))

if latest > last_dt:
    state.setdefault('last_push', {})[channel] = latest.isoformat(timespec='seconds')
    state_file.write_text(json.dumps(state, indent=2))
    print(f'ℹ️ {channel}: push-state synced from formatter log ({latest.isoformat(timespec="seconds")})')
PY
}

log "=== Channel Health Check ==="

if [[ "${RICK_X_SUSPENDED:-false}" == "true" ]]; then
    log "⚪ x: suspended, skipping live timeline check"
elif [[ "${RICK_X_CREDITS_DEPLETED:-false}" == "true" ]]; then
    log "⚪ x: API write credits depleted; monitoring paused until credits are topped up"
else
    X_TIMELINE_JSON=$(bash -lc 'source ~/.config/x-api/keys.env 2>/dev/null || true; xpost timeline MeetRickAI --count 5 --json' 2>&1) || true
    if grep -qi "Unauthorized\|401" <<<"$X_TIMELINE_JSON"; then
        log "⚠️ x: timeline auth failed (401 Unauthorized) — credentials likely expired or revoked"
        LATEST_X_CREATED_AT=""
    else
        LATEST_X_CREATED_AT=$(python3 -c "import json,sys\ntry:\n    d=json.load(sys.stdin)\nexcept Exception:\n    raise SystemExit(0)\nposts=d.get('data') or []\nprint(posts[0].get('created_at','') if posts else '')" <<<"$X_TIMELINE_JSON" 2>/dev/null || true)
    fi
    sync_x_state_from_live "$LATEST_X_CREATED_AT" 2>&1 | while read -r line; do [[ -n "$line" ]] && log "$line"; done
fi

# Sync Moltbook state from Rick's own live posts (the following feed is too noisy for health checks)
if [[ -z "${MOLTBOOK_API_KEY:-}" ]]; then
    log "⚠️ moltbook: MOLTBOOK_API_KEY missing, skipping live feed sync"
    MOLTBOOK_LIVE=""
else
    MOLTBOOK_LIVE=$(source ~/.openclaw/workspace/config/rick.env 2>/dev/null; curl -s "https://www.moltbook.com/api/v1/posts?author=rick_meetrick&limit=20" -H "X-API-Key: $MOLTBOOK_API_KEY" 2>/dev/null | python3 -c "
import json,sys
from datetime import datetime
d=json.load(sys.stdin)
posts=d.get('posts') or d.get('data') or []
latest=None
for p in posts:
    if (p.get('author',{}) or {}).get('name') != 'rick_meetrick' and p.get('author_id') != '33735d8f-4905-4e8c-ad33-7b54b70c896f':
        continue
    created_at = p.get('created_at')
    if not created_at:
        continue
    try:
        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
    except Exception:
        continue
    if latest is None or dt > latest[0]:
        latest = (dt, created_at)
if latest:
    print(latest[1])
" 2>/dev/null || true)
fi
if [[ -n "$MOLTBOOK_LIVE" ]]; then
    STATE_FILE="$STATE_FILE" LATEST_CREATED_AT="$MOLTBOOK_LIVE" python3 - <<'PY'
import json, os
from datetime import datetime
from pathlib import Path
state_file = Path(os.path.expanduser(os.environ['STATE_FILE']))
live_raw = os.environ['LATEST_CREATED_AT'].strip()
if not live_raw: raise SystemExit(0)
live_dt = datetime.fromisoformat(live_raw.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
state = json.loads(state_file.read_text())
last_raw = state.get('last_push', {}).get('moltbook', '1970-01-01T00:00:00')
last_dt = datetime.fromisoformat(last_raw.replace('Z', '+00:00').replace('+00:00', ''))
if live_dt > last_dt:
    state.setdefault('last_push', {})['moltbook'] = live_dt.isoformat(timespec='seconds')
    state_file.write_text(json.dumps(state, indent=2))
    print(f'ℹ️ moltbook: push-state synced from live feed ({live_dt.isoformat(timespec="seconds")})')
PY
fi

# Formatter logs are written before the underlying send attempt for several
# channels. Treat dispatcher/API confirmations as the posting source of truth.
sync_state_from_formatter_log "moltbook" "$FORMATTER_LOG_DIR/formatter-moltbook.jsonl" 2>&1 | while read -r line; do [[ -n "$line" ]] && log "$line"; done

# Sync channels from outbound-dispatcher logs. Some live send paths do not
# write formatter logs, so push-state can otherwise stay at the epoch forever.
# Observe-mode/disabled formatter results are not real posts and must not
# refresh channel health.
for RUNTIME_DISPATCHER in "$FORMATTER_LOG_DIR/outbound-dispatcher.jsonl" "$RUNTIME_DATA_ROOT/operations/outbound-dispatcher.jsonl"; do
    [[ -f "$RUNTIME_DISPATCHER" ]] || continue
    for _ch in instagram threads linkedin reddit; do
        if [[ "$_ch" == "reddit" && "${RICK_OUTBOUND_REDDIT_LIVE:-}" != "1" ]]; then
            continue
        fi
        STATE_FILE="$STATE_FILE" CHANNEL="$_ch" DISPATCHER="$RUNTIME_DISPATCHER" python3 - <<'PY' 2>&1 | while read -r line; do [[ -n "$line" ]] && log "$line"; done
import json, os
from datetime import datetime
from pathlib import Path

state_file = Path(os.path.expanduser(os.environ['STATE_FILE']))
channel = os.environ['CHANNEL']
dispatcher = Path(os.path.expanduser(os.environ['DISPATCHER']))

latest = None
for line in reversed(dispatcher.read_text(encoding='utf-8', errors='replace').splitlines()):
    if not line.strip():
        continue
    try:
        e = json.loads(line)
    except Exception:
        continue
    if e.get('channel') != channel:
        continue
    if e.get('status') != 'sent':
        continue
    if str(e.get('result') or '').lower() == 'observed-only':
        continue
    ran_at = e.get('ran_at')
    if not ran_at:
        continue
    try:
        dt = datetime.fromisoformat(ran_at.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
    except Exception:
        continue
    latest = dt
    break

if latest is None:
    raise SystemExit(0)

state = json.loads(state_file.read_text())
last_raw = state.get('last_push', {}).get(channel, '1970-01-01T00:00:00')
last_dt = datetime.fromisoformat(last_raw.replace('Z', '+00:00').replace('+00:00', ''))

# For dispatcher-backed channels, status=sent is authoritative. This can
# intentionally move state backwards if an older formatter attempt was
# previously mistaken for a live post.
if latest != last_dt:
    state.setdefault('last_push', {})[channel] = latest.isoformat(timespec='seconds')
    state_file.write_text(json.dumps(state, indent=2))
    print(f'\u2139\ufe0f {channel}: push-state synced from runtime dispatcher ({latest.isoformat(timespec="seconds")})')
PY
    done
done

ALERTS=""
python3 -c "
import json, os, sys
from datetime import datetime, timedelta

with open('$STATE_FILE') as f:
    state = json.load(f)

now = datetime.now()
default_threshold = timedelta(hours=$DEFAULT_ALERT_THRESHOLD_HOURS)
channel_thresholds = json.loads('''$CHANNEL_THRESHOLDS_JSON''')
alerts = []

x_suspended = os.environ.get('RICK_X_SUSPENDED', 'false').lower() == 'true'
x_credits_depleted = os.environ.get('RICK_X_CREDITS_DEPLETED', 'false').lower() == 'true'
live_flags = {
    'instagram': 'RICK_OUTBOUND_INSTAGRAM_LIVE',
    'threads': 'RICK_OUTBOUND_THREADS_LIVE',
    'linkedin': 'RICK_OUTBOUND_LINKEDIN_LIVE',
    'reddit': 'RICK_OUTBOUND_REDDIT_LIVE',
    'moltbook': 'RICK_OUTBOUND_MOLTBOOK_LIVE',
}

for channel, last_str in state.get('last_push', {}).items():
    if channel == 'x' and (x_suspended or x_credits_depleted):
        reason = 'suspended' if x_suspended else 'API credits depleted'
        print(f'⚪ x: {reason}, monitoring paused')
        continue
    flag = live_flags.get(channel)
    if flag and os.environ.get(flag, '0') != '1':
        print(f'⚪ {channel}: live posting paused ({flag}!=1)')
        continue
    try:
        last = datetime.fromisoformat(last_str)
        if last.year <= 1971:
            print(f'⚪ {channel}: not configured yet (epoch default)')
            continue
        gap = now - last
        gap_hours = gap.total_seconds() / 3600
        threshold_hours = channel_thresholds.get(channel, $DEFAULT_ALERT_THRESHOLD_HOURS)
        threshold = timedelta(hours=threshold_hours)
        status = '✅' if gap < threshold else '🚨'
        print(f'{status} {channel}: last post {gap_hours:.1f}h ago (threshold {threshold_hours}h)')
        if gap >= threshold:
            alerts.append(f'{channel} ({gap_hours:.0f}h silent, threshold {threshold_hours}h)')
    except:
        print(f'⚠️ {channel}: no valid timestamp')
        alerts.append(f'{channel} (no data)')

if alerts:
    print(f'ALERT: Channels need attention: {\", \".join(alerts)}')
    sys.exit(1)
else:
    print('All channels healthy.')
" 2>&1 | while read -r line; do log "$line"; done
