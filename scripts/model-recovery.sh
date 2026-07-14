#!/bin/bash
# Model Recovery Script — Self-healing auth-state and provider transitions
#
# Triggers when provider-health-monitor.sh detects:
#   - TRANSITION_DETECTED=true
#   - Anthropic disabledUntil set
#   - Critical provider unavailability
#
# Recovery actions (in order):
#   1. Attempt to clear disabledUntil from auth state (SQLite auth store or legacy JSON)
#   2. Restart gateway if auth-state was modified
#   3. Log recovery attempt + result
#   4. Return status to caller

set -e

DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
AGENT_HOME="${RICK_OPENCLAW_AGENT_HOME:-/Users/rickthebot/.openclaw/agents/main/agent}"
AUTH_STATE="$AGENT_HOME/auth-state.json"
AUTH_DB="$AGENT_HOME/openclaw-agent.sqlite"
RECOVERY_LOG="$DATA_ROOT/operations/model-recovery.jsonl"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
AUTH_BACKEND="missing"

# Ensure log directory
mkdir -p "$(dirname "$RECOVERY_LOG")"

# Helper: log recovery event
log_recovery() {
  local phase=$1 result=$2 details=$3
  {
    echo "{\"ts\":\"$TIMESTAMP\",\"phase\":\"$phase\",\"result\":\"$result\""
    [ -n "$details" ] && echo ",$details"
    echo "}"
  } | tr -d '\n' >> "$RECOVERY_LOG"
  echo "" >> "$RECOVERY_LOG"
}

detect_auth_backend() {
  if [ -f "$AUTH_STATE" ]; then
    AUTH_BACKEND="json"
    return 0
  fi

  if [ -f "$AUTH_DB" ] && sqlite3 "$AUTH_DB" "select 1 from auth_profile_state where state_key='primary' limit 1;" >/dev/null 2>&1; then
    AUTH_BACKEND="sqlite"
    return 0
  fi

  AUTH_BACKEND="missing"
  return 1
}

auth_state_json() {
  case "$AUTH_BACKEND" in
    json)
      cat "$AUTH_STATE"
      ;;
    sqlite)
      sqlite3 "$AUTH_DB" "select state_json from auth_profile_state where state_key='primary' limit 1;"
      ;;
    *)
      return 1
      ;;
  esac
}

clear_auth_disable() {
  case "$AUTH_BACKEND" in
    json)
      TEMP_STATE=$(mktemp)
      if jq 'del(."usageStats"."anthropic:default".disabledUntil,
                ."usageStats"."anthropic:default".disabledReason,
                ."usageStats"."anthropic:default".failureCounts)' "$AUTH_STATE" > "$TEMP_STATE" 2>/dev/null; then
        mv "$TEMP_STATE" "$AUTH_STATE"
        return 0
      fi
      rm -f "$TEMP_STATE"
      return 1
      ;;
    sqlite)
      sqlite3 "$AUTH_DB" "update auth_profile_state set state_json = json_remove(state_json, '$.\"usageStats\".\"anthropic:default\".disabledUntil', '$.\"usageStats\".\"anthropic:default\".disabledReason', '$.\"usageStats\".\"anthropic:default\".failureCounts'), updated_at = cast(strftime('%s','now') as integer) * 1000 where state_key='primary';"
      ;;
    *)
      return 1
      ;;
  esac
}

echo "[model-recovery] Starting recovery flow at $TIMESTAMP"
detect_auth_backend || true
echo "[model-recovery] Auth backend: $AUTH_BACKEND"

# ─────────────────────────────────────────────────────────────────────────
# Phase 1: Back up current auth-state
# ─────────────────────────────────────────────────────────────────────────

BACKUP_FILE=""
if [ "$AUTH_BACKEND" = "json" ]; then
  BACKUP_FILE="${AUTH_STATE}.recovery-$(date +%s).backup"
  cp "$AUTH_STATE" "$BACKUP_FILE"
  echo "[model-recovery] Backed up JSON auth-state to $BACKUP_FILE"
  log_recovery "backup" "success" "\"backend\":\"json\",\"backup_path\":\"$BACKUP_FILE\""
elif [ "$AUTH_BACKEND" = "sqlite" ]; then
  BACKUP_FILE="${AUTH_DB}.recovery-$(date +%s).backup"
  sqlite3 "$AUTH_DB" ".backup '$BACKUP_FILE'"
  echo "[model-recovery] Backed up SQLite auth-state to $BACKUP_FILE"
  log_recovery "backup" "success" "\"backend\":\"sqlite\",\"backup_path\":\"$BACKUP_FILE\""
else
  echo "[model-recovery] WARNING: No auth-state found at $AUTH_STATE or $AUTH_DB"
  log_recovery "backup" "missing" "\"json_path\":\"$AUTH_STATE\",\"sqlite_path\":\"$AUTH_DB\""
fi

# ─────────────────────────────────────────────────────────────────────────
# Phase 2: Check for disabledUntil and attempt to clear it
# ─────────────────────────────────────────────────────────────────────────

HAS_DISABLE=false
DISABLED_UNTIL=""

if [ "$AUTH_BACKEND" != "missing" ]; then
  DISABLED_UNTIL=$(auth_state_json | jq -r '."usageStats"."anthropic:default".disabledUntil // empty' 2>/dev/null || echo "")
  if [ -n "$DISABLED_UNTIL" ]; then
    HAS_DISABLE=true
    echo "[model-recovery] Found disabledUntil=$DISABLED_UNTIL; attempting to clear..."
    log_recovery "detect_disable" "found" "\"backend\":\"$AUTH_BACKEND\",\"disabled_until\":\"$DISABLED_UNTIL\""
    
    if clear_auth_disable; then
      echo "[model-recovery] Cleared disabledUntil from auth-state"
      log_recovery "clear_disable" "success" "\"previous_value\":\"$DISABLED_UNTIL\""
    else
      echo "[model-recovery] ERROR: Failed to clear disabledUntil"
      log_recovery "clear_disable" "failed" "\"backend\":\"$AUTH_BACKEND\",\"reason\":\"auth_state_update_failed\""
      exit 1
    fi
  else
    echo "[model-recovery] No disabledUntil found; checking for other transitions..."
    log_recovery "detect_disable" "none" "\"backend\":\"$AUTH_BACKEND\",\"message\":\"clean\""
  fi
else
  echo "[model-recovery] No auth backend available; skipping auth-state mutation"
  log_recovery "detect_disable" "skipped" "\"reason\":\"auth_state_missing\""
fi

# ─────────────────────────────────────────────────────────────────────────
# Phase 3: Test Anthropic API connectivity (verify fix)
# ─────────────────────────────────────────────────────────────────────────

ANTHROPIC_KEY="${ANTHROPIC_API_KEY:-}"
TEST_RESPONSE="000"

if [ -z "$ANTHROPIC_KEY" ]; then
  echo "[model-recovery] WARNING: ANTHROPIC_API_KEY not set; skipping connectivity test"
  log_recovery "test_api" "skipped" "\"reason\":\"no_api_key\""
else
  echo "[model-recovery] Testing Anthropic API connectivity..."
  TEST_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "https://api.anthropic.com/v1/messages" \
    -H "x-api-key: $ANTHROPIC_KEY" \
    -H "anthropic-version: 2023-06-01" \
    -H "Content-Type: application/json" \
    -d '{"model":"claude-opus-4-8","max_tokens":10,"messages":[{"role":"user","content":"ok"}]}' \
    2>/dev/null || echo "000")
  
  if [ "$TEST_RESPONSE" = "200" ] || [ "$TEST_RESPONSE" = "201" ]; then
    echo "[model-recovery] ✓ Anthropic API is responding (HTTP $TEST_RESPONSE)"
    log_recovery "test_api" "success" "\"http_code\":\"$TEST_RESPONSE\""
  else
    echo "[model-recovery] WARNING: Anthropic API returned HTTP $TEST_RESPONSE"
    log_recovery "test_api" "warning" "\"http_code\":\"$TEST_RESPONSE\""
    if [ "$HAS_DISABLE" = "true" ] && [ -n "$BACKUP_FILE" ] && [ -f "$BACKUP_FILE" ]; then
      if [ "$AUTH_BACKEND" = "json" ]; then
        cp "$BACKUP_FILE" "$AUTH_STATE"
      elif [ "$AUTH_BACKEND" = "sqlite" ]; then
        sqlite3 "$AUTH_DB" ".restore '$BACKUP_FILE'"
      fi
      echo "[model-recovery] Restored disabled auth-state because API probe did not succeed"
      log_recovery "restore_disable" "success" "\"reason\":\"api_probe_failed\",\"http_code\":\"$TEST_RESPONSE\""
    fi
  fi
fi

# ─────────────────────────────────────────────────────────────────────────
# Phase 4: Soft restart of gateway if auth-state was modified
# ─────────────────────────────────────────────────────────────────────────

if [ "$HAS_DISABLE" = "true" ]; then
  echo "[model-recovery] Attempting to notify gateway of auth-state change..."
  
  # Use OpenClaw's event system or just touch a sentinel file that gateway monitors
  # For now, we log the recovery completion and let the gateway's next heartbeat
  # pick up the cleared auth-state.
  
  # Optional: Try to send a signal to any running gateway processes
  if pgrep -f "openclaw.*gateway" > /dev/null 2>&1; then
    echo "[model-recovery] Found running gateway processes; changes will take effect on next request"
    log_recovery "gateway_notify" "running" "\"note\":\"auth_state_cleared_will_be_read_on_next_request\""
  else
    echo "[model-recovery] No running gateway found; recovery staged for next start"
    log_recovery "gateway_notify" "idle" "\"note\":\"recovery_will_apply_on_next_gateway_start\""
  fi
else
  echo "[model-recovery] No auth-state modifications needed"
  log_recovery "gateway_notify" "skipped" "\"reason\":\"no_changes\""
fi

# ─────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────

echo "[model-recovery] Recovery flow complete"
log_recovery "complete" "success" "\"backend\":\"$AUTH_BACKEND\",\"backup\":\"$BACKUP_FILE\",\"cleared_disable\":$HAS_DISABLE,\"api_test\":\"$TEST_RESPONSE\""

export RECOVERY_EXECUTED=true
export RECOVERY_CLEARED_DISABLE=$HAS_DISABLE
export RECOVERY_BACKUP=$BACKUP_FILE

exit 0
