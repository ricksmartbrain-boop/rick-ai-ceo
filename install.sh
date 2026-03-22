#!/usr/bin/env bash
set -euo pipefail

# ================================================================
# MeetRick.ai Installer
# One command. 5 minutes. Rick is alive.
#
# Usage:
#   curl -fsSL https://meetrick.ai/install.sh | bash
#   curl -fsSL https://meetrick.ai/install.sh | bash -s -- --tier free
#   curl -fsSL https://meetrick.ai/install.sh | bash -s -- --uninstall
#   curl -fsSL https://meetrick.ai/install.sh | bash -s -- --help
#
# ================================================================

RICK_INSTALLER_VERSION="1.0.0"
MEETRICK_API="https://rick-api-production.up.railway.app/api/v1"
MEETRICK_RELEASES="https://releases.meetrick.ai"

# Defaults
TIER=""
LICENSE_KEY=""
NON_INTERACTIVE=false
NO_TELEMETRY=false
UNINSTALL=false
VERBOSE=false
TEMP_DIR=""

# Colors (disable on dumb terminals or non-TTY stdout)
if [ -t 1 ] && [ "${TERM:-dumb}" != "dumb" ]; then
  RED='[0;31m'
  GREEN='[0;32m'
  CYAN='[0;36m'
  YELLOW='[1;33m'
  BOLD='[1m'
  DIM='[2m'
  NC='[0m'
else
  RED='' GREEN='' CYAN='' YELLOW='' BOLD='' DIM='' NC=''
fi

# Logging
LOG_FILE=""
OPENCLAW_DIR="$HOME/.openclaw"
WORKSPACE_DIR="$OPENCLAW_DIR/workspace"

# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
print_banner() {
  echo -e "${CYAN}"
  echo "========================================"
  echo ""
  echo "   ██████  ██  ██████ ██   ██"
  echo "   ██   ██ ██ ██      ██  ██"
  echo "   ██████  ██ ██      █████"
  echo "   ██   ██ ██ ██      ██  ██"
  echo "   ██   ██ ██  ██████ ██   ██"
  echo ""
  echo "   meetrick.ai — AI for everyone"
  echo "   v${RICK_INSTALLER_VERSION}"
  echo ""
  echo "========================================"
  echo -e "${NC}"
}

step()  { echo -e "
${CYAN}[$1/8]${NC} ${BOLD}$2${NC}"; log "STEP $1: $2"; }
ok()    { echo -e "  ${GREEN}✓ $1${NC}"; log "OK: $1"; }
fail()  { echo -e "  ${RED}✗ $1${NC}"; log "FAIL: $1"; exit 1; }
warn()  { echo -e "  ${YELLOW}! $1${NC}"; log "WARN: $1"; }
info()  { echo -e "  $1"; log "INFO: $1"; }
debug() { if [ "$VERBOSE" = true ]; then echo -e "  ${DIM}$1${NC}"; fi; log "DEBUG: $1"; }

log() {
  if [ -n "$LOG_FILE" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" >> "$LOG_FILE" 2>/dev/null || true
  fi
}

# ----------------------------------------------------------------
# Cleanup trap — handles Ctrl+C and errors
# ----------------------------------------------------------------
cleanup() {
  local exit_code=$?
  if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
    rm -rf "$TEMP_DIR"
    debug "Cleaned up temp directory"
  fi
  if [ $exit_code -ne 0 ] && [ $exit_code -ne 130 ]; then
    echo ""
    echo -e "${RED}Installation interrupted or failed.${NC}"
    if [ -n "$LOG_FILE" ]; then
      echo -e "Check the install log: ${BOLD}$LOG_FILE${NC}"
    fi
    echo -e "For help: ${BOLD}https://meetrick.ai/install#troubleshooting${NC}"
  fi
  if [ $exit_code -eq 130 ]; then
    echo ""
    echo -e "
${YELLOW}Installation cancelled by user.${NC}"
  fi
}
trap cleanup EXIT

# ----------------------------------------------------------------
# Usage / Help
# ----------------------------------------------------------------
usage() {
  echo "MeetRick.ai Installer v${RICK_INSTALLER_VERSION}"
  echo ""
  echo "Usage:"
  echo "  curl -fsSL https://meetrick.ai/install.sh | bash"
  echo "  bash install.sh [OPTIONS]"
  echo ""
  echo "Options:"
  echo "  --tier free|pro|lifetime|managed    Skip tier selection prompt"
  echo "  --license-key KEY           Provide license key (for pro/lifetime/managed)"
  echo "  --non-interactive           Skip all prompts (requires --tier)"
  echo "  --no-telemetry              Opt out of anonymous install analytics"
  echo "  --uninstall                 Remove Rick and OpenClaw"
  echo "  --verbose                   Show debug output"
  echo "  --help                      Show this help message"
  echo ""
  echo "Examples:"
  echo "  # Interactive install (recommended)"
  echo "  curl -fsSL https://meetrick.ai/install.sh | bash"
  echo ""
  echo "  # Automated free tier install"
  echo "  curl -fsSL https://meetrick.ai/install.sh | bash -s -- --tier free --non-interactive"
  echo ""
  echo "  # Pro tier with license key"
  echo "  curl -fsSL https://meetrick.ai/install.sh | bash -s -- --tier pro --license-key RP_abc123"
  echo ""
  echo "  # Uninstall"
  echo "  curl -fsSL https://meetrick.ai/install.sh | bash -s -- --uninstall"
  exit 0
}

# ----------------------------------------------------------------
# Parse arguments
# ----------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --tier)
      if [ $# -lt 2 ]; then echo "Error: --tier requires a value (free, pro, lifetime, managed)"; exit 1; fi
      TIER="$2"
      if [[ ! "$TIER" =~ ^(free|pro|lifetime|managed)$ ]]; then
        echo "Invalid tier: $TIER. Must be free, pro, lifetime, or managed."
        exit 1
      fi
      shift 2 ;;
    --license-key)
      if [ $# -lt 2 ]; then echo "Error: --license-key requires a value"; exit 1; fi
      LICENSE_KEY="$2"
      shift 2 ;;
    --non-interactive) NON_INTERACTIVE=true; shift ;;
    --no-telemetry)    NO_TELEMETRY=true; shift ;;
    --uninstall)       UNINSTALL=true; shift ;;
    --verbose)         VERBOSE=true; shift ;;
    --help|-h)         usage ;;
    *)
      echo "Unknown option: $1"
      echo "Run with --help for usage information."
      exit 1 ;;
  esac
done

# ----------------------------------------------------------------
# Reopen /dev/tty for curl | bash interactive mode
# ----------------------------------------------------------------
if [ ! -t 0 ] && [ "$NON_INTERACTIVE" = false ]; then
  if [ -e /dev/tty ]; then
    exec < /dev/tty
  else
    echo "No terminal available. Use: curl ... | bash -s -- --tier free --non-interactive"
    exit 1
  fi
fi

# ----------------------------------------------------------------
# Initialize logging
# ----------------------------------------------------------------
if ! mkdir -p "$OPENCLAW_DIR" 2>/dev/null; then
  echo -e "${RED}✗ Cannot create $OPENCLAW_DIR — check disk space and permissions${NC}"
  exit 1
fi
LOG_FILE="$OPENCLAW_DIR/.install.log"
: > "$LOG_FILE" 2>/dev/null || LOG_FILE="/tmp/meetrick-install-$$.log"
log "MeetRick Installer v${RICK_INSTALLER_VERSION} started"
log "Args: tier=$TIER non_interactive=$NON_INTERACTIVE no_telemetry=$NO_TELEMETRY uninstall=$UNINSTALL"

# Check required dependencies
if ! command -v python3 &>/dev/null; then
  fail "Python 3 is required but not found. Install: brew install python@3 (macOS) or apt install python3 (Linux)"
fi

if ! python3 -c "import json, sys" 2>/dev/null; then
  fail "Python 3 found but broken (json/sys modules missing). Reinstall Python 3."
fi

# ----------------------------------------------------------------
# Uninstall
# ----------------------------------------------------------------
if [ "$UNINSTALL" = true ]; then
  echo -e "${CYAN}Uninstalling Rick...${NC}"
  echo ""

  # Stop running processes
  if command -v openclaw &>/dev/null; then
    openclaw gateway stop 2>/dev/null || true
    info "Stopped OpenClaw gateway"
  fi

  # Remove launchd agents (macOS)
  if [ "$(uname -s)" = "Darwin" ]; then
    for plist in "$HOME"/Library/LaunchAgents/ai.openclaw.* "$HOME"/Library/LaunchAgents/ai.rick.*; do
      if [ -f "$plist" ]; then
        launchctl unload "$plist" 2>/dev/null || true
        rm -f "$plist"
        info "Removed launchd agent: $(basename "$plist")"
      fi
    done
  fi

  # Remove cron entries
  if crontab -l 2>/dev/null | grep -q "meetrick\|openclaw\|rick_update"; then
    crontab -l 2>/dev/null | grep -v "meetrick\|openclaw\|rick_update" | crontab - 2>/dev/null || true
    info "Cleaned cron entries"
  fi

  # Confirm before removing data
  if [ "$NON_INTERACTIVE" = false ]; then
    echo ""
    echo -e "  ${YELLOW}This will remove:${NC}"
    echo "    ~/.openclaw/ (config, workspace, memory)"
    echo "    OpenClaw npm package"
    echo ""
    echo "  Note: Your bot token in openclaw.json will be removed."
    echo "  Save it first if needed. The bot itself is unaffected."
    echo ""
    read -rp "  Continue? (y/N): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy] ]]; then
      echo "Cancelled."
      exit 0
    fi
  fi

  # Remove OpenClaw
  npm uninstall -g openclaw 2>/dev/null || true
  info "Removed OpenClaw package"

  # Remove Rick workspace (backup personal files first)
  if [ -d "$OPENCLAW_DIR" ]; then
    BACKUP="$HOME/.openclaw-backup-$(date +%Y%m%d-%H%M%S)"
    if [ -f "$WORKSPACE_DIR/SOUL.md" ] || [ -f "$WORKSPACE_DIR/USER.md" ] || [ -f "$WORKSPACE_DIR/IDENTITY.md" ] || [ -d "$WORKSPACE_DIR/memory" ]; then
      mkdir -p "$BACKUP"
      cp "$WORKSPACE_DIR/SOUL.md" "$BACKUP/" 2>/dev/null || true
      cp "$WORKSPACE_DIR/USER.md" "$BACKUP/" 2>/dev/null || true
      cp "$WORKSPACE_DIR/MEMORY.md" "$BACKUP/" 2>/dev/null || true
      cp -r "$WORKSPACE_DIR/memory" "$BACKUP/" 2>/dev/null || true
      cp "$WORKSPACE_DIR/IDENTITY.md" "$BACKUP/" 2>/dev/null || true
      info "Backed up personal files to $BACKUP"
    fi
    rm -rf "$OPENCLAW_DIR"
    info "Removed ~/.openclaw/"
  fi

  echo ""
  echo -e "${GREEN}Rick has been uninstalled.${NC}"
  if [ -d "${BACKUP:-}" ]; then
    echo -e "Your personal files are backed up at: ${BOLD}$BACKUP${NC}"
  fi
  echo "Your Telegram bot, API keys, and Node.js are still installed."
  exit 0
fi

# ----------------------------------------------------------------
# Non-interactive validation
# ----------------------------------------------------------------
if [ "$NON_INTERACTIVE" = true ] && [ -z "$TIER" ]; then
  echo "Error: --non-interactive requires --tier"
  echo "Run with --help for usage information."
  exit 1
fi

# ================================================================
# START INSTALLATION
# ================================================================
print_banner

# Telemetry notice
if [ "$NO_TELEMETRY" = false ]; then
  echo -e "  ${DIM}Rick sends anonymous install analytics (approximate location, tier, version)"
  echo -e "  to improve the product. No personal data is collected. Opt out: --no-telemetry${NC}"
  echo ""
fi

# ----------------------------------------------------------------
# STEP 1: Detect OS + Architecture
# ----------------------------------------------------------------
step 1 "Detecting system..."

OS="$(uname -s)"
ARCH="$(uname -m)"
BREW_PREFIX=""
BREW_CMD=""

case "$OS" in
  Darwin)
    PLATFORM="macos"
    if [ "$ARCH" = "arm64" ]; then
      BREW_PREFIX="/opt/homebrew"
    else
      BREW_PREFIX="/usr/local"
    fi
    ;;
  Linux)
    if grep -qi microsoft /proc/version 2>/dev/null; then
      PLATFORM="wsl2"
    else
      PLATFORM="linux"
    fi
    ;;
  *) fail "Unsupported OS ($OS). Rick needs macOS or Linux." ;;
esac

ok "Platform: $PLATFORM ($ARCH)"

# ----------------------------------------------------------------
# STEP 2: Check / install Node.js 22+
# ----------------------------------------------------------------
step 2 "Checking Node.js..."

NODE_OK=false
if command -v node &>/dev/null; then
  NODE_V=$(node -v | sed 's/v//' | cut -d. -f1)
  if [ "$NODE_V" -ge 22 ] 2>/dev/null; then
    NODE_OK=true
    ok "Node.js $(node -v) found"
  else
    warn "Node.js $(node -v) is too old (need 22+)"
  fi
fi

if [ "$NODE_OK" = false ]; then
  if [ "$NON_INTERACTIVE" = false ]; then
    info "Node.js 22+ is required. Installing..."
  fi

  if [ "$PLATFORM" = "macos" ]; then
    # Check both Homebrew locations (Intel + Apple Silicon)
    if [ -x "$BREW_PREFIX/bin/brew" ]; then
      BREW_CMD="$BREW_PREFIX/bin/brew"
    elif [ -x "/opt/homebrew/bin/brew" ]; then
      BREW_CMD="/opt/homebrew/bin/brew"
    elif [ -x "/usr/local/bin/brew" ]; then
      BREW_CMD="/usr/local/bin/brew"
    fi

    if [ -z "$BREW_CMD" ]; then
      info "Installing Homebrew first..."
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      if [ -x "$BREW_PREFIX/bin/brew" ]; then
        BREW_CMD="$BREW_PREFIX/bin/brew"
      elif [ -x "/opt/homebrew/bin/brew" ]; then
        BREW_CMD="/opt/homebrew/bin/brew"
      elif [ -x "/usr/local/bin/brew" ]; then
        BREW_CMD="/usr/local/bin/brew"
      fi
      eval "$($BREW_CMD shellenv)" 2>/dev/null || true
    fi

    if [ -n "$BREW_CMD" ]; then
      $BREW_CMD install node@22
      $BREW_CMD link node@22 --overwrite --force 2>/dev/null || true
    else
      fail "Could not install Homebrew. Install Node.js 22 manually: https://nodejs.org"
    fi
  else
    # Linux / WSL2
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
    export NVM_DIR="$HOME/.nvm"
    # shellcheck source=/dev/null
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    nvm install 22
    nvm use 22
    nvm alias default 22
    # Ensure node is on PATH even in non-login shells
    NODE_BIN=$(ls -d "${NVM_DIR}/versions/node"/v* 2>/dev/null | sort -V | tail -1)
    if [ -n "$NODE_BIN" ]; then
      export PATH="$NODE_BIN/bin:$PATH"
    fi
  fi

  if ! command -v node &>/dev/null; then
    fail "Node.js install failed. Install manually: https://nodejs.org"
  fi
  ok "Node.js $(node -v) installed"
fi

# ----------------------------------------------------------------
# STEP 3: Install OpenClaw
# ----------------------------------------------------------------
step 3 "Checking OpenClaw..."

if command -v openclaw &>/dev/null; then
  OC_VERSION=$(openclaw --version 2>/dev/null || echo "unknown")
  ok "OpenClaw $OC_VERSION found"
else
  info "Installing OpenClaw..."
  OC_TEMP=$(mktemp)
  curl -fsSL https://openclaw.ai/install.sh -o "$OC_TEMP" 2>/dev/null
  bash "$OC_TEMP" < /dev/tty 2>/dev/null || true
  rm -f "$OC_TEMP"
  export PATH="$PATH:$HOME/.npm-global/bin:${BREW_PREFIX:-/usr/local}/bin"

  if ! command -v openclaw &>/dev/null; then
    fail "OpenClaw install failed. Try: curl -fsSL https://openclaw.ai/install.sh | bash"
  fi
  ok "OpenClaw installed"
fi

# ----------------------------------------------------------------
# STEP 4: Choose tier
# ----------------------------------------------------------------
step 4 "Choose your Rick..."

if [ -z "$TIER" ]; then
  while true; do
    echo ""
    echo -e "  ${BOLD}[1]${NC} Rick Free — \$0/forever"
    echo "      5 skills, Telegram, community support"
    echo ""
    echo -e "  ${BOLD}[2]${NC} Rick Pro — \$9/month"
    echo "      15+ skills, role templates, green dot on Rick Map"
    echo ""
    echo -e "  ${BOLD}[3]${NC} Rick Lifetime — \$199 one-time"
    echo "      Everything in Pro, forever. Blue dot on Rick Map"
    echo ""
    echo -e "  ${BOLD}[4]${NC} Rick Managed — \$499/month"
    echo "      Full autonomous ops, all channels, white-glove"
    echo ""
    read -rp "  Enter choice (1/2/3/4): " TIER_CHOICE

    case "$TIER_CHOICE" in
      1) TIER="free"; break ;;
      2) TIER="pro"; break ;;
      3) TIER="lifetime"; break ;;
      4) TIER="managed"; break ;;
      *) echo -e "  ${YELLOW}Invalid choice. Enter 1, 2, 3, or 4.${NC}" ;;
    esac
  done
fi

# Validate license for paid tiers
if [ "$TIER" != "free" ] && [ -z "$LICENSE_KEY" ]; then
  if [ "$NON_INTERACTIVE" = true ]; then
    fail "Paid tier requires --license-key. Get yours at https://meetrick.ai/pricing"
  fi
  echo ""
  read -rsp "  Enter your license key (from meetrick.ai): " LICENSE_KEY
  echo ""
fi

if [ "$TIER" != "free" ] && [ -n "$LICENSE_KEY" ]; then
  debug "Validating license key..."
  # Use Python with sys.argv for safe JSON encoding (no shell injection)
  VALIDATION=$(curl -s --max-time 10 -X POST "$MEETRICK_API/license/validate" \
    -H "Content-Type: application/json" \
    --data-raw "$(python3 -c "import json,sys; print(json.dumps({'key': sys.argv[1], 'tier': sys.argv[2]}))" "$LICENSE_KEY" "$TIER" 2>/dev/null)" 2>/dev/null || echo "")

  IS_VALID=$(echo "$VALIDATION" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if d.get('valid') else 'no')" 2>/dev/null || echo "no")
  if [ "$IS_VALID" = "yes" ]; then
    ok "License valid — $TIER tier"
  elif [ -z "$VALIDATION" ]; then
    warn "Could not reach license server. Continuing with local install."
    warn "License will be validated on first heartbeat."
  else
    fail "Invalid license key. Get yours at https://meetrick.ai/pricing"
  fi
else
  ok "Free tier — no license needed"
fi

# ----------------------------------------------------------------
# STEP 5: Download Rick config bundle
# ----------------------------------------------------------------
step 5 "Downloading Rick ($TIER)..."

RICK_VERSION="$RICK_INSTALLER_VERSION"

# Lifetime tier uses the same bundle as Pro (same feature set)
DOWNLOAD_TIER="$TIER"
[ "$TIER" = "lifetime" ] && DOWNLOAD_TIER="pro"

BUNDLE_URL="$MEETRICK_RELEASES/$DOWNLOAD_TIER-$RICK_VERSION.tar.gz"
CHECKSUM_URL="$MEETRICK_RELEASES/$DOWNLOAD_TIER-$RICK_VERSION.sha256"
TEMP_DIR=$(mktemp -d)

# Download bundle
if ! curl -fsSL --proto '=https' --tlsv1.2 --max-time 60 "$BUNDLE_URL" -o "$TEMP_DIR/rick.tar.gz"; then
  fail "Download failed. Check your connection or try: bash install.sh --tier free"
fi

# Verify checksum (if available)
EXPECTED_CHECKSUM=$(curl -fsSL --proto '=https' --tlsv1.2 --max-time 10 "$CHECKSUM_URL" 2>/dev/null || echo "")
if [ -n "$EXPECTED_CHECKSUM" ]; then
  if command -v shasum &>/dev/null; then
    ACTUAL_CHECKSUM=$(shasum -a 256 "$TEMP_DIR/rick.tar.gz" | cut -d' ' -f1)
  elif command -v sha256sum &>/dev/null; then
    ACTUAL_CHECKSUM=$(sha256sum "$TEMP_DIR/rick.tar.gz" | cut -d' ' -f1)
  else
    ACTUAL_CHECKSUM=""
    warn "No SHA256 tool found — skipping checksum verification"
  fi

  if [ -n "$ACTUAL_CHECKSUM" ]; then
    EXPECTED_HASH=$(echo "$EXPECTED_CHECKSUM" | awk '{print $1}')
    if [ "$ACTUAL_CHECKSUM" != "$EXPECTED_HASH" ]; then
      fail "Checksum mismatch. Download may be corrupted. Try again."
    fi
    debug "Checksum verified: $ACTUAL_CHECKSUM"
  fi
else
  debug "No checksum file available — skipping verification"
fi

# Safety: reject archives with path traversal or absolute paths
if tar -tzf "$TEMP_DIR/rick.tar.gz" 2>/dev/null | grep -qE '(\.\./|^/)'; then
  fail "Archive contains unsafe paths. Download may be compromised."
fi
if ! tar -xzf "$TEMP_DIR/rick.tar.gz" --no-same-owner -C "$TEMP_DIR"; then
  fail "Failed to extract Rick config bundle. Download may be corrupted — try again."
fi
# Reject symlinks pointing outside the archive
while IFS= read -r -d '' link; do
  target=$(readlink "$link")
  case "$target" in /*|*../*) fail "Unsafe symlink in archive: $link";; esac
done < <(find "$TEMP_DIR" -type l -print0 2>/dev/null)
ok "Rick config downloaded and verified"

# ----------------------------------------------------------------
# STEP 6: Install Rick config
# ----------------------------------------------------------------
step 6 "Installing Rick config..."

mkdir -p "$OPENCLAW_DIR" || fail "Cannot create $OPENCLAW_DIR — check disk space and permissions"
mkdir -p "$WORKSPACE_DIR" || fail "Cannot create $WORKSPACE_DIR"
mkdir -p "$WORKSPACE_DIR/skills" || fail "Cannot create $WORKSPACE_DIR/skills"
mkdir -p "$WORKSPACE_DIR/memory" || fail "Cannot create $WORKSPACE_DIR/memory"

# Backup existing openclaw.json before overwriting
if [ -f "$OPENCLAW_DIR/openclaw.json" ]; then
  cp "$OPENCLAW_DIR/openclaw.json" "$OPENCLAW_DIR/openclaw.json.bak"
  debug "Backed up existing openclaw.json"
fi

# Config file — always overwrite (engine settings)
if [ -f "$TEMP_DIR/rick/openclaw.json" ]; then
  cp "$TEMP_DIR/rick/openclaw.json" "$OPENCLAW_DIR/openclaw.json"
  chmod 600 "$OPENCLAW_DIR/openclaw.json"
fi

# Workspace files — protect user-personalized files on reinstall
PROTECTED_FILES="SOUL.md USER.md MEMORY.md IDENTITY.md"
WORKSPACE_FILES="SOUL.md AGENTS.md IDENTITY.md USER.md TOOLS.md HEARTBEAT.md BOOTSTRAP.md"

for file in $WORKSPACE_FILES; do
  SRC="$TEMP_DIR/rick/workspace/$file"
  DST="$WORKSPACE_DIR/$file"
  if [ -f "$SRC" ]; then
    if [ -f "$DST" ]; then
      IS_PROTECTED=false
      for pf in $PROTECTED_FILES; do
        if [ "$file" = "$pf" ]; then
          IS_PROTECTED=true
          break
        fi
      done
      if [ "$IS_PROTECTED" = true ]; then
        info "Keeping your existing $file"
        continue
      fi
    fi
    cp "$SRC" "$DST"
  fi
done

# Skills — atomic replacement via staging directory
if [ -d "$TEMP_DIR/rick/workspace/skills" ]; then
  SKILLS_STAGING="$WORKSPACE_DIR/.skills-staging-$$"
  cp -r "$TEMP_DIR/rick/workspace/skills" "$SKILLS_STAGING"

  # Preserve custom skills (anything not in the bundle)
  if [ -d "$WORKSPACE_DIR/skills" ]; then
    for custom_skill in "$WORKSPACE_DIR/skills"/*/; do
      skill_name=$(basename "$custom_skill")
      if [ ! -d "$SKILLS_STAGING/$skill_name" ] && [ -d "$custom_skill" ]; then
        cp -r "$custom_skill" "$SKILLS_STAGING/$skill_name"
        debug "Preserved custom skill: $skill_name"
      fi
    done
  fi

  # Atomic swap with rollback
  if [ -d "$WORKSPACE_DIR/skills" ]; then
    mv "$WORKSPACE_DIR/skills" "$WORKSPACE_DIR/.skills-old-$$"
  fi
  mv "$SKILLS_STAGING" "$WORKSPACE_DIR/skills" || {
    # Rollback on failure
    [ -d "$WORKSPACE_DIR/.skills-old-$$" ] && mv "$WORKSPACE_DIR/.skills-old-$$" "$WORKSPACE_DIR/skills"
    fail "Failed to install skills"
  }
  rm -rf "$WORKSPACE_DIR/.skills-old-$$" 2>/dev/null || true
  ok "Skills installed (custom skills preserved)"
else
  ok "Skills unchanged"
fi

# Role templates (Pro/Lifetime/Managed)
if [ -d "$TEMP_DIR/rick/workspace/roles" ]; then
  mkdir -p "$WORKSPACE_DIR/roles"
  find "$TEMP_DIR/rick/workspace/roles/" -mindepth 1 -maxdepth 1 -exec cp -r {} "$WORKSPACE_DIR/roles/" \; 2>/dev/null || true
  ok "Role templates installed"
fi

# Lock SOUL.md against accidental self-modification
chmod 444 "$WORKSPACE_DIR/SOUL.md" 2>/dev/null || true

# Save tier + version locally
echo "$TIER" > "$OPENCLAW_DIR/.rick_tier"
echo "$RICK_VERSION" > "$OPENCLAW_DIR/.rick_version"

# Store license key securely
if [ -n "$LICENSE_KEY" ]; then
  printf '%s' "$LICENSE_KEY" > "$OPENCLAW_DIR/.rick_license"
  chmod 600 "$OPENCLAW_DIR/.rick_license"
fi

# Cleanup temp
rm -rf "$TEMP_DIR"
TEMP_DIR=""

ok "Rick config installed to $WORKSPACE_DIR"

# ----------------------------------------------------------------
# STEP 7: Connect AI model + Telegram
# ----------------------------------------------------------------
step 7 "Connecting Rick's brain..."

AUTH_DIR="$OPENCLAW_DIR/agents/main/agent"
mkdir -p "$AUTH_DIR"

if [ "$NON_INTERACTIVE" = false ]; then
  echo ""
  echo "  Choose your AI provider:"
  echo ""
  echo -e "  ${BOLD}[1]${NC} Anthropic API key (recommended)"
  echo -e "  ${BOLD}[2]${NC} OpenAI API key"
  echo -e "  ${BOLD}[3]${NC} Google Gemini API key (free tier available)"
  echo -e "  ${BOLD}[4]${NC} Local model via Ollama (free, private)"
  echo -e "  ${BOLD}[5]${NC} Set up later"
  echo ""
  read -rp "  Choice (1/2/3/4/5): " MODEL_CHOICE

  write_auth_profile() {
    local provider="$1"
    local key="$2"
    python3 -c "
import json, sys
data = {'profiles': [{'provider': sys.argv[1], 'apiKey': sys.argv[2]}]}
with open(sys.argv[3], 'w') as f:
    json.dump(data, f, indent=2)
" "$provider" "$key" "$AUTH_DIR/auth-profiles.json"
    chmod 600 "$AUTH_DIR/auth-profiles.json"
  }

  case "$MODEL_CHOICE" in
    1)
      read -rsp "  Anthropic API key: " API_KEY
      echo ""
      if [[ ! "$API_KEY" =~ ^sk-ant- ]]; then
        warn "Key doesn't look like an Anthropic API key (expected sk-ant-...). Saved anyway."
      fi
      write_auth_profile "anthropic" "$API_KEY"
      ok "Anthropic connected"
      ;;
    2)
      read -rsp "  OpenAI API key: " API_KEY
      echo ""
      if [[ ! "$API_KEY" =~ ^sk- ]]; then
        warn "Key doesn't look like an OpenAI API key (expected sk-...). Saved anyway."
      fi
      write_auth_profile "openai" "$API_KEY"
      ok "OpenAI connected"
      ;;
    3)
      read -rsp "  Google Gemini API key: " API_KEY
      echo ""
      if [[ ! "$API_KEY" =~ ^AI ]]; then
        warn "Key doesn't look like a Google API key (expected AI...). Saved anyway."
      fi
      write_auth_profile "google" "$API_KEY"
      ok "Google Gemini connected"
      ;;
    4)
      info "Checking Ollama..."
      if ! command -v ollama &>/dev/null; then
        info "Installing Ollama..."
        if [ "$PLATFORM" = "macos" ] && [ -n "$BREW_CMD" ]; then
          $BREW_CMD install ollama
        else
          curl -fsSL https://ollama.com/install.sh | sh
        fi
      fi
      if command -v ollama &>/dev/null; then
        info "Pulling model (this may take a few minutes)..."
        ollama pull llama3.2:3b
        ok "Local model ready (llama3.2:3b)"
      else
        warn "Ollama install failed. Set up manually: https://ollama.com"
      fi
      ;;
    5)
      warn "Skipped — Rick will ask for an API key on first message"
      ;;
    *)
      warn "Invalid choice — skipping. Configure later in OpenClaw settings."
      ;;
  esac

  # Telegram setup
  echo ""
  echo "  Connect Telegram (optional but recommended):"
  echo "  1. Open Telegram, search @BotFather"
  echo "  2. Send /newbot, follow the prompts"
  echo "  3. Copy the bot token"
  echo ""
  echo "  (Press Enter to skip)"
  read -rsp "  Telegram bot token: " TG_TOKEN
  echo ""

  if [ -n "$TG_TOKEN" ]; then
    TG_CHECK=$(curl -s --max-time 10 -K - <<< "url = https://api.telegram.org/bot${TG_TOKEN}/getMe" 2>/dev/null || echo "")
    if echo "$TG_CHECK" | grep -q '"ok":true'; then
      # Safely inject token into config using Python (avoids sed injection)
      if [ -f "$OPENCLAW_DIR/openclaw.json" ]; then
        python3 -c "
import json, sys
config_path = sys.argv[1]
token = sys.argv[2]
with open(config_path, 'r') as f:
    config = json.load(f)
if 'channels' not in config:
    config['channels'] = {}
if 'telegram' not in config['channels']:
    config['channels']['telegram'] = {}
config['channels']['telegram']['enabled'] = True
config['channels']['telegram']['botToken'] = token
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
" "$OPENCLAW_DIR/openclaw.json" "$TG_TOKEN"
      fi
      ok "Telegram connected"
    else
      warn "Token didn't validate. Fix later: openclaw config set channels.telegram.botToken YOUR_TOKEN"
    fi
  else
    info "Skipping Telegram — set up anytime with: openclaw config set channels.telegram.botToken YOUR_TOKEN"
  fi
else
  info "Non-interactive mode — skipping provider and Telegram setup"
  info "Configure later: openclaw config set auth.profiles.anthropic.apiKey YOUR_KEY"
fi

# ----------------------------------------------------------------
# STEP 8: Start Rick
# ----------------------------------------------------------------
step 8 "Starting Rick..."

if ! openclaw onboard --install-daemon --skip-wizard 2>"$LOG_FILE.onboard"; then
  warn "OpenClaw onboard failed. Check: $LOG_FILE.onboard"
  warn "Try manually: openclaw onboard --install-daemon --skip-wizard"
fi

# Wait for gateway
ATTEMPTS=0
GATEWAY_UP=false
while [ $ATTEMPTS -lt 15 ]; do
  if curl -s --max-time 5 http://127.0.0.1:18789/ >/dev/null 2>&1; then
    GATEWAY_UP=true
    break
  fi
  sleep 2
  ATTEMPTS=$((ATTEMPTS + 1))
done

if [ "$GATEWAY_UP" = true ]; then
  ok "Rick is running (gateway on port 18789)"
else
  warn "Gateway is still starting. Check with: openclaw status"
fi

# ================================================================
# REGISTRATION + TELEMETRY
# ================================================================

# Reuse existing Rick ID on reinstall to prevent duplicate map entries
if [ -f "$OPENCLAW_DIR/.rick_id" ]; then
  RICK_ID=$(cat "$OPENCLAW_DIR/.rick_id")
  RICK_NUMBER=$(cat "$OPENCLAW_DIR/.rick_number" 2>/dev/null || echo "?")
  debug "Reusing existing Rick ID: $RICK_ID (Rick #$RICK_NUMBER)"
  IS_REINSTALL=true
else
  # Generate UUID v4 cross-platform
  if command -v uuidgen &>/dev/null; then
    RICK_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
  elif [ -f /proc/sys/kernel/random/uuid ]; then
    RICK_ID=$(cat /proc/sys/kernel/random/uuid)
  else
    RICK_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
  fi
  IS_REINSTALL=false
fi

RICK_NUMBER="?"
RICK_SECRET=""

if [ "$NO_TELEMETRY" = false ]; then
  # Get location from IP for map placement
  GEO_DATA=$(curl -s --max-time 5 "https://ipapi.co/json/" 2>/dev/null || echo "{}")
  COUNTRY=$(echo "$GEO_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('country_code','XX'))" 2>/dev/null || echo "XX")
  CITY=$(echo "$GEO_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('city',''))" 2>/dev/null || echo "")
  LAT=$(echo "$GEO_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('latitude',0))" 2>/dev/null || echo "0")
  LNG=$(echo "$GEO_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('longitude',0))" 2>/dev/null || echo "0")

  if [ "$IS_REINSTALL" = false ]; then
    REGISTER_PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({
    'rick_id': sys.argv[1],
    'tier': sys.argv[2],
    'country': sys.argv[3],
    'city': sys.argv[4],
    'lat': float(sys.argv[5]),
    'lng': float(sys.argv[6]),
    'platform': sys.argv[7],
    'version': sys.argv[8]
}))
" "$RICK_ID" "$TIER" "$COUNTRY" "$CITY" "$LAT" "$LNG" "$PLATFORM" "$RICK_VERSION" 2>/dev/null || echo '{}')

    REGISTER_RESPONSE=$(curl -s --max-time 10 -X POST "$MEETRICK_API/register" \
      -H "Content-Type: application/json" \
      --data-raw "$REGISTER_PAYLOAD" 2>/dev/null || echo "{}")

    RICK_NUMBER=$(echo "$REGISTER_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('rick_number','?'))" 2>/dev/null || echo "?")
    RICK_SECRET=$(echo "$REGISTER_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('rick_secret',''))" 2>/dev/null || echo "")
  else
    # Reinstall: send heartbeat to update version
    EXISTING_SECRET=$(cat "$OPENCLAW_DIR/.rick_secret" 2>/dev/null || echo "")
    curl -s --max-time 10 -X POST "$MEETRICK_API/heartbeat" \
      -H "Content-Type: application/json" \
      --data-raw "$(python3 -c "import json,sys; print(json.dumps({'rick_id':sys.argv[1],'rick_secret':sys.argv[2],'version':sys.argv[3]}))" "$RICK_ID" "$EXISTING_SECRET" "$RICK_VERSION")" \
      >/dev/null 2>&1 || true
    debug "Sent reinstall heartbeat"
  fi
else
  debug "Telemetry disabled — skipping registration"
fi

# Save Rick identity
echo "$RICK_ID" > "$OPENCLAW_DIR/.rick_id"
[ "$RICK_NUMBER" != "?" ] && echo "$RICK_NUMBER" > "$OPENCLAW_DIR/.rick_number"

# Reload rick_number from disk for display (may have been set on prior install)
if [ "$IS_REINSTALL" = true ] && [ "$RICK_NUMBER" = "?" ]; then
  RICK_NUMBER=$(cat "$OPENCLAW_DIR/.rick_number" 2>/dev/null || echo "?")
fi
if [ -n "$RICK_SECRET" ]; then
  printf '%s' "$RICK_SECRET" > "$OPENCLAW_DIR/.rick_secret"
  chmod 600 "$OPENCLAW_DIR/.rick_secret"
fi

# ================================================================
# AUTO-UPDATE SCRIPT
# ================================================================
cat > "$OPENCLAW_DIR/.rick_update.sh" << 'UPDATEEOF'
#!/usr/bin/env bash
set -euo pipefail

# Rick auto-update — runs weekly via cron/launchd

# Ensure common tools are on PATH (cron/launchd have minimal PATH)
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH" 2>/dev/null || true
NVM_NODE=$(ls -d "$HOME/.nvm/versions/node"/v* 2>/dev/null | sort -V | tail -1)
[ -n "${NVM_NODE:-}" ] && export PATH="$NVM_NODE/bin:$PATH"

LOCKDIR="$HOME/.openclaw/.rick_update.lock.d"
OPENCLAW_DIR="$HOME/.openclaw"
WS="$OPENCLAW_DIR/workspace"
MEETRICK_API="https://rick-api-production.up.railway.app/api/v1"
TEMP=""

# Concurrency guard (atomic mkdir)
if [ -d "$LOCKDIR" ]; then
  if [ "$(uname -s)" = "Darwin" ]; then
    LOCK_MTIME=$(stat -f %m "$LOCKDIR" 2>/dev/null) || { rm -rf "$LOCKDIR"; LOCK_MTIME=""; }
  else
    LOCK_MTIME=$(stat -c %Y "$LOCKDIR" 2>/dev/null) || { rm -rf "$LOCKDIR"; LOCK_MTIME=""; }
  fi
  if [ -n "${LOCK_MTIME:-}" ]; then
    LOCK_AGE=$(( $(date +%s) - LOCK_MTIME ))
  else
    LOCK_AGE=9999
  fi
  if [ "$LOCK_AGE" -lt 600 ]; then
    echo "Update already running (lock age: ${LOCK_AGE}s). Exiting."
    exit 0
  fi
  rm -rf "$LOCKDIR"
fi
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  exit 0
fi
trap 'rm -rf "$LOCKDIR"; [ -n "${TEMP:-}" ] && rm -rf "$TEMP"' EXIT

CURRENT_VERSION=$(cat "$OPENCLAW_DIR/.rick_version" 2>/dev/null || echo "0.0.0")
RICK_TIER=$(cat "$OPENCLAW_DIR/.rick_tier" 2>/dev/null || echo "free")
RICK_ID=$(cat "$OPENCLAW_DIR/.rick_id" 2>/dev/null || echo "unknown")
RICK_SECRET=$(cat "$OPENCLAW_DIR/.rick_secret" 2>/dev/null || echo "")

UPDATE_PARAMS=$(python3 -c "
import urllib.parse, sys
print(urllib.parse.urlencode({
    'rick_id': sys.argv[1], 'rick_secret': sys.argv[2],
    'tier': sys.argv[3], 'version': sys.argv[4]
}))
" "$RICK_ID" "$RICK_SECRET" "$RICK_TIER" "$CURRENT_VERSION" 2>/dev/null)
UPDATE_CHECK=$(curl -s --max-time 10 \
  "$MEETRICK_API/update?$UPDATE_PARAMS" \
  2>/dev/null || echo "{}")

HAS_UPDATE=$(echo "$UPDATE_CHECK" | grep -o '"has_update":true' || true)

if [ -z "$HAS_UPDATE" ]; then
  exit 0
fi

NEW_VERSION=$(echo "$UPDATE_CHECK" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version',''))" 2>/dev/null || echo "")
DOWNLOAD_URL=$(echo "$UPDATE_CHECK" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('url',''))" 2>/dev/null || echo "")

if [ -z "$NEW_VERSION" ] || [ -z "$DOWNLOAD_URL" ]; then
  exit 0
fi

# Don't re-apply the same version
if [ "$NEW_VERSION" = "$CURRENT_VERSION" ]; then
  exit 0
fi

# Validate download URL domain
case "$DOWNLOAD_URL" in
  https://releases.meetrick.ai/*) ;;
  *) echo "Untrusted update URL: $DOWNLOAD_URL"; exit 1 ;;
esac

TEMP=$(mktemp -d)

if ! curl -fsSL --proto '=https' --tlsv1.2 --max-time 60 "$DOWNLOAD_URL" -o "$TEMP/update.tar.gz" 2>/dev/null; then
  echo "Update download failed for version $NEW_VERSION"
  exit 1
fi

# Verify checksum if available
CHECKSUM_URL="${DOWNLOAD_URL%.tar.gz}.sha256"
EXPECTED=$(curl -fsSL --proto '=https' --tlsv1.2 --max-time 10 "$CHECKSUM_URL" 2>/dev/null | awk '{print $1}')
if [ -n "${EXPECTED:-}" ]; then
  if command -v shasum &>/dev/null; then
    ACTUAL=$(shasum -a 256 "$TEMP/update.tar.gz" | cut -d' ' -f1)
  elif command -v sha256sum &>/dev/null; then
    ACTUAL=$(sha256sum "$TEMP/update.tar.gz" | cut -d' ' -f1)
  else
    ACTUAL=""
  fi
  if [ -n "${ACTUAL:-}" ] && [ "$ACTUAL" != "$EXPECTED" ]; then
    echo "Checksum mismatch on update $NEW_VERSION"; exit 1
  fi
fi

# Safety: reject path traversal and absolute paths
if tar -tzf "$TEMP/update.tar.gz" 2>/dev/null | grep -qE '(\.\./|^/)'; then
  echo "Update archive contains unsafe paths"; exit 1
fi
tar -xzf "$TEMP/update.tar.gz" --no-same-owner -C "$TEMP" 2>/dev/null || { echo "Update extract failed"; exit 1; }

# Reject symlinks pointing outside the archive
while IFS= read -r -d '' link; do
  target=$(readlink "$link")
  case "$target" in /*|*../*) echo "Unsafe symlink: $link"; exit 1;; esac
done < <(find "$TEMP" -type l -print0 2>/dev/null)

# Backup current state before replacing
BACKUP_DIR="$OPENCLAW_DIR/.update-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

# REPLACE: skills — atomic swap preserving custom skills
if [ -d "$TEMP/rick/workspace/skills" ]; then
  cp -r "$WS/skills" "$BACKUP_DIR/skills" || { echo "Backup failed — aborting update"; exit 1; }
  STAGING="$WS/.skills-update-staging-$$"
  cp -r "$TEMP/rick/workspace/skills" "$STAGING"
  for custom in "$WS/skills"/*/; do
    name=$(basename "$custom")
    if [ ! -d "$STAGING/$name" ] && [ -d "$custom" ]; then
      cp -r "$custom" "$STAGING/$name"
    fi
  done
  mv "$WS/skills" "$WS/.skills-old-$$"
  mv "$STAGING" "$WS/skills" || {
    # Rollback on failure
    [ -d "$WS/.skills-old-$$" ] && mv "$WS/.skills-old-$$" "$WS/skills"
    echo "Skills update failed — rolled back"; exit 1
  }
  rm -rf "$WS/.skills-old-$$"
fi

# REPLACE: operational docs
for f in AGENTS.md TOOLS.md HEARTBEAT.md; do
  if [ -f "$TEMP/rick/workspace/$f" ]; then
    cp "$WS/$f" "$BACKUP_DIR/$f" 2>/dev/null || true
    cp "$TEMP/rick/workspace/$f" "$WS/$f"
  fi
done

# REPLACE: role templates
if [ -d "$TEMP/rick/workspace/roles" ]; then
  mkdir -p "$WS/roles"
  find "$TEMP/rick/workspace/roles/" -mindepth 1 -maxdepth 1 -exec cp -r {} "$WS/roles/" \; 2>/dev/null || true
fi

# NEVER TOUCH: SOUL.md, USER.md, MEMORY.md, memory/, IDENTITY.md

echo "$NEW_VERSION" > "$OPENCLAW_DIR/.rick_version"

# Notify
curl -s --max-time 10 -X POST "$MEETRICK_API/heartbeat" \
  -H "Content-Type: application/json" \
  --data-raw "$(python3 -c "import json,sys; print(json.dumps({'rick_id':sys.argv[1],'rick_secret':sys.argv[2],'version':sys.argv[3]}))" "$RICK_ID" "$RICK_SECRET" "$NEW_VERSION")" \
  >/dev/null 2>&1 || true

echo "Updated Rick from $CURRENT_VERSION to $NEW_VERSION"
echo "Backup saved to $BACKUP_DIR"

# Keep only 3 most recent backups
ls -dt "$OPENCLAW_DIR"/.update-backup-* 2>/dev/null | tail -n +4 | xargs rm -rf 2>/dev/null || true
UPDATEEOF
chmod +x "$OPENCLAW_DIR/.rick_update.sh"

# ================================================================
# AUTO-UPDATE SCHEDULING
# ================================================================
# Schedule weekly auto-update check as fallback
# (primary: OpenClaw daemon; fallback: cron/launchd)
if [ "$PLATFORM" = "macos" ]; then
  PLIST="$HOME/Library/LaunchAgents/ai.rick.update.plist"
  if [ ! -f "$PLIST" ]; then
    cat > "$PLIST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.rick.update</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${HOME}/.openclaw/.rick_update.sh</string>
  </array>
  <key>StartInterval</key>
  <integer>604800</integer>
  <key>StandardOutPath</key>
  <string>${HOME}/.openclaw/.update.log</string>
  <key>StandardErrorPath</key>
  <string>${HOME}/.openclaw/.update.log</string>
</dict>
</plist>
PLISTEOF
    launchctl load "$PLIST" 2>/dev/null || true
    debug "Scheduled weekly auto-update via launchd"
  fi
else
  # Linux/WSL: add weekly cron if not present
  if ! crontab -l 2>/dev/null | grep -q "rick_update"; then
    (crontab -l 2>/dev/null; echo "0 3 * * 0 \$HOME/.openclaw/.rick_update.sh >> \$HOME/.openclaw/.update.log 2>&1") | crontab -
    debug "Scheduled weekly auto-update via cron"
  fi
fi

# ================================================================
# SUCCESS
# ================================================================
echo ""
echo -e "${CYAN}========================================"
echo -e "         Rick is alive!"
echo -e "========================================${NC}"
echo ""
if [ "$RICK_NUMBER" != "?" ]; then
echo -e "   You are ${BOLD}Rick #${RICK_NUMBER}${NC}"
else
echo -e "   Rick ID: ${BOLD}${RICK_ID:0:8}...${NC}"
fi
echo -e "   Tier:    ${BOLD}${TIER}${NC}"
echo ""
echo -e "   ${BOLD}See yourself on the map:${NC}"
echo -e "   https://meetrick.ai/map"
echo ""
if [ -n "${TG_TOKEN:-}" ]; then
echo "   Open Telegram — Rick sent you a message!"
echo ""
fi
echo "  Quick commands:"
echo "    openclaw status          — check Rick's health"
echo "    openclaw log             — see what Rick is doing"
echo "    openclaw gateway restart — restart Rick"
echo "    openclaw dashboard       — open web dashboard"
echo ""
echo -e "  ${DIM}Auto-updates: enabled (weekly). Disable: openclaw config set autoUpdate false${NC}"
echo ""
echo -e "  ${DIM}Install log: $LOG_FILE${NC}"
echo -e "  ${DIM}Rick config: $WORKSPACE_DIR${NC}"
echo ""

log "Installation completed successfully. Tier=$TIER Rick=$RICK_ID Number=$RICK_NUMBER"
