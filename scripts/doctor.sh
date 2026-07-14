#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${RICK_ENV_FILE:-$ROOT_DIR/config/rick.env}"
QUIET=false

if [[ "${1:-}" == "--quiet" ]]; then
  QUIET=true
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
export RICK_XPOST_BIN="${RICK_XPOST_BIN:-$ROOT_DIR/bin/xpost}"
mkdir -p "$RICK_DATA_ROOT/control"

REPORT_FILE="$RICK_DATA_ROOT/control/dependency-gaps.md"
TMP_FILE="$(mktemp)"

required_bins=(pnpm python3 tmux jq curl gh himalaya codex claude ralphy xpost vercel stripe openclaw)
# CRITICAL vars only — things that actually break Rick if missing
required_envs=(RICK_DATA_ROOT RICK_TELEGRAM_BOT_TOKEN STRIPE_SECRET_KEY OPENAI_API_KEY ANTHROPIC_API_KEY RESEND_API_KEY)
# Optional vars — warn only, never block
optional_envs=(RICK_NEWSLETTER_PLATFORM RICK_X_SUSPENDED LINKEDIN_ACCESS_TOKEN REDDIT_CLIENT_ID XAI_API_KEY GOOGLE_API_KEY MEMELORD_API_KEY)
# Model aliases are informational only — routing has hardcoded fallbacks
required_model_aliases=()

missing_bins=()
missing_envs=()
missing_files=()
invalid_jsons=()
placeholder_files=()
missing_model_aliases=()
missing_llm_access=()
warnings=()

for bin in "${required_bins[@]}"; do
  if [[ "$bin" == "xpost" && -x "${RICK_XPOST_BIN/#\~/$HOME}" ]]; then
    continue
  fi
  if ! command -v "$bin" >/dev/null 2>&1; then
    missing_bins+=("$bin")
  fi
done

for placeholder_path in \
  "$RICK_DATA_ROOT/control/founder-profile.md" \
  "$RICK_DATA_ROOT/control/access-inventory.md" \
  "$RICK_DATA_ROOT/control/heartbeat-targets.md" \
  "$RICK_DATA_ROOT/control/launch-playbook.md"; do
  if [[ ! -f "$placeholder_path" ]]; then
    placeholder_files+=("missing:$placeholder_path")
  elif grep -q '\[TODO' "$placeholder_path"; then
    placeholder_files+=("unfinished:$placeholder_path")
  fi
done

for env_name in "${required_envs[@]}"; do
  value="${!env_name:-}"
  if [[ -z "$value" ]]; then
    missing_envs+=("$env_name")
    continue
  fi

  case "$env_name" in
    RICK_STRIPE_ACCOUNTS_FILE|RICK_SITES_FILE|RICK_APPROVAL_POLICY_FILE|RICK_LANE_POLICY_FILE|RICK_WATCHDOG_PROCESSES_FILE|RICK_TOKEN_BUDGET_FILE|RICK_MODEL_PRICING_FILE|RICK_PORTFOLIO_FILE|RICK_PORTFOLIO_SCORECARDS_FILE|RICK_OPENCLAW_SESSION_POLICY_FILE|RICK_OPENCLAW_AGENT_BLUEPRINT_FILE)
      expanded="${value/#\~/$HOME}"
      if [[ ! -f "$expanded" ]]; then
        missing_files+=("$env_name:$expanded")
      elif [[ "$expanded" == *.json ]] && ! jq empty "$expanded" >/dev/null 2>&1; then
        invalid_jsons+=("$env_name:$expanded")
      fi
      ;;
    RICK_OPENCLAW_MEMORY_FLUSH_PROMPT_FILE)
      expanded="${value/#\~/$HOME}"
      if [[ ! -f "$expanded" ]]; then
        missing_files+=("$env_name:$expanded")
      fi
      ;;
  esac
done

thread_mode="${RICK_TELEGRAM_THREAD_MODE:-off}"
if [[ "$thread_mode" != "off" ]]; then
  if [[ -z "${RICK_TELEGRAM_FORUM_CHAT_ID:-}" ]]; then
    missing_envs+=("RICK_TELEGRAM_FORUM_CHAT_ID")
  fi
  if [[ -z "${RICK_TELEGRAM_TOPICS_FILE:-}" ]]; then
    missing_envs+=("RICK_TELEGRAM_TOPICS_FILE")
  else
    expanded="${RICK_TELEGRAM_TOPICS_FILE/#\~/$HOME}"
    if [[ ! -f "$expanded" ]]; then
      missing_files+=("RICK_TELEGRAM_TOPICS_FILE:$expanded")
    elif ! jq empty "$expanded" >/dev/null 2>&1; then
      invalid_jsons+=("RICK_TELEGRAM_TOPICS_FILE:$expanded")
    fi
  fi
  if [[ -z "${RICK_OPENCLAW_SESSION_POLICY_FILE:-}" ]]; then
    warnings+=("thread mode enabled but RICK_OPENCLAW_SESSION_POLICY_FILE is empty")
  else
    expanded="${RICK_OPENCLAW_SESSION_POLICY_FILE/#\~/$HOME}"
    if [[ ! -f "$expanded" ]]; then
      warnings+=("thread mode enabled but session policy file is missing: $expanded")
    fi
  fi
fi

secure_dm_mode="${RICK_OPENCLAW_SECURE_DM_MODE:-prepared}"
case "$secure_dm_mode" in
  off|prepared|enabled)
    ;;
  *)
    missing_envs+=("RICK_OPENCLAW_SECURE_DM_MODE (expected: off, prepared, enabled)")
    ;;
esac

if [[ "$secure_dm_mode" == "enabled" ]]; then
  if [[ -z "${RICK_OPENCLAW_SESSION_POLICY_FILE:-}" ]]; then
    warnings+=("secure DM mode enabled without RICK_OPENCLAW_SESSION_POLICY_FILE")
  else
    expanded="${RICK_OPENCLAW_SESSION_POLICY_FILE/#\~/$HOME}"
    if [[ ! -f "$expanded" ]]; then
      warnings+=("secure DM mode enabled but session policy file is missing: $expanded")
    fi
  fi
  if [[ -z "${RICK_OPENCLAW_AGENT_BLUEPRINT_FILE:-}" ]]; then
    warnings+=("secure DM mode enabled without RICK_OPENCLAW_AGENT_BLUEPRINT_FILE")
  else
    expanded="${RICK_OPENCLAW_AGENT_BLUEPRINT_FILE/#\~/$HOME}"
    if [[ ! -f "$expanded" ]]; then
      warnings+=("secure DM mode enabled but agent blueprint file is missing: $expanded")
    fi
  fi
fi

for env_name in "${required_model_aliases[@]:-}"; do
  if [[ -z "${!env_name:-}" ]]; then
    missing_model_aliases+=("$env_name")
  fi
done

if [[ -n "${RICK_LLM_GATEWAY_URL:-}" ]]; then
  if [[ -z "${RICK_LLM_GATEWAY_API_KEY:-}" ]]; then
    missing_llm_access+=("RICK_LLM_GATEWAY_API_KEY")
  fi
else
  [[ -z "${OPENAI_API_KEY:-}" ]] && missing_llm_access+=("OPENAI_API_KEY")
  [[ -z "${ANTHROPIC_API_KEY:-}" ]] && missing_llm_access+=("ANTHROPIC_API_KEY")
  if [[ -z "${GOOGLE_API_KEY:-}" && -z "${GEMINI_API_KEY:-}" ]]; then
    missing_llm_access+=("GOOGLE_API_KEY or GEMINI_API_KEY")
  fi
  [[ -z "${XAI_API_KEY:-}" ]] && missing_llm_access+=("XAI_API_KEY")
fi

# --- Autonomy stack checks ---
autonomy_issues=()

if ! crontab -l 2>/dev/null | grep -q 'RICK_CRON_BEGIN'; then
  autonomy_issues+=("Crons not installed (missing RICK_CRON_BEGIN block)")
fi
if ! launchctl list 2>/dev/null | grep -q 'ai.rick.daemon'; then
  autonomy_issues+=("Daemon not loaded (ai.rick.daemon)")
fi
if ! launchctl list 2>/dev/null | grep -q 'ai.rick.demo-video-weekly'; then
  autonomy_issues+=("Demo video weekly not loaded (ai.rick.demo-video-weekly)")
fi

DAEMON_LOG="${RICK_DATA_ROOT:-$HOME/rick-vault}/logs/daemon.log"
if [[ -f "$DAEMON_LOG" ]]; then
  log_age_seconds=$(( $(date +%s) - $(stat -f %m "$DAEMON_LOG" 2>/dev/null || echo 0) ))
  if [[ $log_age_seconds -gt 300 ]]; then
    autonomy_issues+=("Daemon log stale ($(( log_age_seconds / 60 )) min since last write)")
  fi
else
  autonomy_issues+=("Daemon log not found: $DAEMON_LOG")
fi

{
  echo "# Dependency Gaps"
  echo
  echo "Generated: $(date '+%Y-%m-%d %H:%M:%S')"
  echo

  if [[ ${#missing_bins[@]} -eq 0 && ${#missing_envs[@]} -eq 0 && ${#missing_files[@]} -eq 0 && ${#invalid_jsons[@]} -eq 0 && ${#placeholder_files[@]} -eq 0 && ${#missing_model_aliases[@]} -eq 0 && ${#missing_llm_access[@]} -eq 0 && ${#warnings[@]} -eq 0 && ${#autonomy_issues[@]} -eq 0 ]]; then
    echo "No missing dependencies detected."
  else
    if [[ ${#missing_bins[@]} -gt 0 ]]; then
      echo "## Missing CLIs"
      echo
      for item in "${missing_bins[@]}"; do
        echo "- $item"
      done
      echo
    fi

    if [[ ${#missing_envs[@]} -gt 0 ]]; then
      echo "## Missing Environment Values"
      echo
      for item in "${missing_envs[@]}"; do
        echo "- $item"
      done
      echo
    fi

    if [[ ${#missing_files[@]} -gt 0 ]]; then
      echo "## Missing Config Files"
      echo
      for item in "${missing_files[@]}"; do
        echo "- $item"
      done
      echo
    fi

    if [[ ${#invalid_jsons[@]} -gt 0 ]]; then
      echo "## Invalid JSON Configs"
      echo
      for item in "${invalid_jsons[@]}"; do
        echo "- $item"
      done
      echo
    fi

    if [[ ${#placeholder_files[@]} -gt 0 ]]; then
      echo "## Unfinished Founder Placeholders"
      echo
      for item in "${placeholder_files[@]}"; do
        echo "- $item"
      done
      echo
    fi

    if [[ ${#missing_model_aliases[@]} -gt 0 ]]; then
      echo "## Missing Model Alias Values"
      echo
      for item in "${missing_model_aliases[@]}"; do
        echo "- $item"
      done
      echo
    fi

    if [[ ${#missing_llm_access[@]} -gt 0 ]]; then
      echo "## Missing LLM Access"
      echo
      for item in "${missing_llm_access[@]}"; do
        echo "- $item"
      done
      echo
    fi

    if [[ ${#warnings[@]} -gt 0 ]]; then
      echo "## Warnings"
      echo
      for item in "${warnings[@]}"; do
        echo "- $item"
      done
      echo
    fi

    if [[ ${#autonomy_issues[@]} -gt 0 ]]; then
      echo "## Autonomy Stack"
      echo
      for item in "${autonomy_issues[@]}"; do
        echo "- $item"
      done
      echo
    fi

    echo "## User Action Required"
    echo
    echo "Rick should not stall here. Resolve these items or log them in approvals if user action is required."
  fi
} > "$TMP_FILE"

mv "$TMP_FILE" "$REPORT_FILE"

if [[ "$QUIET" == "false" ]]; then
  cat "$REPORT_FILE"
fi
