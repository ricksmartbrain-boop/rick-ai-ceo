#!/bin/bash
# Provider Health Monitor + Auth State Checker
# Runs every 15 min via cron (212043fc-815c-46db-97f9-e9943a74a023)
#
# Checks:
#   1. Anthropic auth-state for TRANSITION_DETECTED or disabledUntil
#   2. OpenAI API key validity (quick ping)
#   3. OpenRouter fallback availability
#
# Emits:
#   TRANSITION_DETECTED=true   → auth state has unstable transitions
#   AUTH_ISSUE=<provider>      → <provider> disabled or unreachable
#   CLEAN=true                 → all providers healthy
#
# Log: ~/.openclaw/workspace/operations/provider-health.jsonl

set -e

DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
AGENT_HOME="${RICK_OPENCLAW_AGENT_HOME:-/Users/rickthebot/.openclaw/agents/main/agent}"
AUTH_STATE="$AGENT_HOME/auth-state.json"
AUTH_DB="$AGENT_HOME/openclaw-agent.sqlite"
OP_LOG="$DATA_ROOT/operations/provider-health.jsonl"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Ensure log directory
mkdir -p "$(dirname "$OP_LOG")"

# Helper: log an event
log_event() {
  local status=$1 event=$2 details=$3
  {
    echo "{\"ts\":\"$TIMESTAMP\",\"status\":\"$status\",\"event\":\"$event\""
    [ -n "$details" ] && echo ",$details"
    echo "}"
  } | tr -d '\n' >> "$OP_LOG"
  echo "" >> "$OP_LOG"
}

auth_state_json() {
  if [ -f "$AUTH_STATE" ]; then
    cat "$AUTH_STATE"
    return 0
  fi

  if [ -f "$AUTH_DB" ]; then
    sqlite3 "$AUTH_DB" "select state_json from auth_profile_state where state_key='primary' limit 1;" 2>/dev/null
    return 0
  fi

  return 1
}

# ─────────────────────────────────────────────────────────────────────────
# 1. Check Anthropic auth-state for known failure patterns
# ─────────────────────────────────────────────────────────────────────────

ANTHROPIC_STATUS="clean"
TRANSITION_DETECTED=false

AUTH_JSON=$(auth_state_json || true)
if [ -n "$AUTH_JSON" ]; then
  # Check for disabledUntil key (means billing watchdog has muted the provider)
  DISABLED_UNTIL=$(printf '%s' "$AUTH_JSON" | jq -r '."usageStats"."anthropic:default".disabledUntil // empty' 2>/dev/null || echo "")
  if [ -n "$DISABLED_UNTIL" ]; then
    ANTHROPIC_STATUS="disabled"
    log_event "warning" "anthropic_disabled" "\"disabled_until\":\"$DISABLED_UNTIL\",\"reason\":\"auth_state_disabled\""
  fi
  
  # Check for known transition markers that indicate a recent auth event
  # (e.g., credential rotation, account change, rate-limit reset in progress)
  if printf '%s' "$AUTH_JSON" | grep -q '"_transition"' 2>/dev/null; then
    TRANSITION_DETECTED=true
    log_event "warning" "anthropic_transition" "\"transition_detected\":true"
  fi
else
  log_event "warning" "auth_state_missing" "\"json_path\":\"$AUTH_STATE\",\"sqlite_path\":\"$AUTH_DB\""
fi

# ─────────────────────────────────────────────────────────────────────────
# 2. OpenAI quick connectivity check (don't consume quota)
# ─────────────────────────────────────────────────────────────────────────

OPENAI_STATUS="clean"
OPENAI_KEY="${OPENAI_API_KEY:-}"

if [ -z "$OPENAI_KEY" ]; then
  OPENAI_STATUS="unconfigured"
  log_event "warning" "openai_unconfigured" "\"message\":\"OPENAI_API_KEY not set\""
else
  # Quick metadata request to the API (no token cost, just checking auth)
  OPENAI_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $OPENAI_KEY" \
    "https://api.openai.com/v1/models" \
    2>/dev/null || echo "000")
  
  case "$OPENAI_RESPONSE" in
    200|401|429)
      # 401 = auth failure (bad key)
      # 429 = rate limited (key is valid but quota exhausted)
      # 200 = success
      if [ "$OPENAI_RESPONSE" = "401" ]; then
        OPENAI_STATUS="auth_failed"
        log_event "error" "openai_auth_failed" "\"http_code\":\"401\""
      elif [ "$OPENAI_RESPONSE" = "429" ]; then
        OPENAI_STATUS="rate_limited"
        log_event "warning" "openai_rate_limited" "\"http_code\":\"429\""
      else
        OPENAI_STATUS="clean"
      fi
      ;;
    *)
      OPENAI_STATUS="unreachable"
      log_event "error" "openai_unreachable" "\"http_code\":\"$OPENAI_RESPONSE\""
      ;;
  esac
fi

# ─────────────────────────────────────────────────────────────────────────
# 3. OpenRouter fallback readiness (config check only, no API call)
# ─────────────────────────────────────────────────────────────────────────

OPENROUTER_STATUS="clean"
OPENROUTER_KEY="${OPENROUTER_API_KEY:-}"

if [ -z "$OPENROUTER_KEY" ]; then
  OPENROUTER_STATUS="unconfigured"
  log_event "warning" "openrouter_unconfigured" "\"message\":\"OPENROUTER_API_KEY not set\""
else
  # Assume it's configured and ready; no API call (OpenRouter is fallback only)
  OPENROUTER_STATUS="ready"
fi

# ─────────────────────────────────────────────────────────────────────────
# Summary: Determine recovery trigger
# ─────────────────────────────────────────────────────────────────────────

OVERALL_STATUS="clean"
NEEDS_RECOVERY=false

if [ "$ANTHROPIC_STATUS" != "clean" ] || [ "$TRANSITION_DETECTED" = "true" ]; then
  OVERALL_STATUS="anthropic_issue"
  NEEDS_RECOVERY=true
fi

if [ "$OPENAI_STATUS" = "auth_failed" ] || [ "$OPENAI_STATUS" = "unreachable" ]; then
  OVERALL_STATUS="openai_issue"
  NEEDS_RECOVERY=false  # Don't recover for OpenAI; fallback to OpenRouter
fi

if [ "$OPENROUTER_STATUS" = "unconfigured" ]; then
  OVERALL_STATUS="critical_no_fallback"
  NEEDS_RECOVERY=true
fi

# ─────────────────────────────────────────────────────────────────────────
# Final log entry + exit codes
# ─────────────────────────────────────────────────────────────────────────

log_event "ok" "monitor_complete" "\"overall_status\":\"$OVERALL_STATUS\",\"anthropic\":\"$ANTHROPIC_STATUS\",\"openai\":\"$OPENAI_STATUS\",\"openrouter\":\"$OPENROUTER_STATUS\",\"transition_detected\":$TRANSITION_DETECTED,\"needs_recovery\":$NEEDS_RECOVERY"

# Export for calling shell/subagent
export TRANSITION_DETECTED
export OVERALL_STATUS
export NEEDS_RECOVERY

if [ "$NEEDS_RECOVERY" = "true" ]; then
  exit 1  # Signal recovery needed
else
  exit 0  # All clean
fi
