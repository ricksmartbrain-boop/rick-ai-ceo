#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RICK_ENV_FILE:-$ROOT_DIR/config/rick.env}"
AUTO_YES=0
SKIP_CRONS=0

usage() {
  cat <<'USAGE'
Usage: scripts/setup.sh [--yes] [--skip-crons]

Interactive bootstrap for Rick v6 on a dedicated Mac Mini / Mac Studio.

Options:
  -y, --yes         Run non-interactively with default yes answers
      --skip-crons  Skip cron installation step
  -h, --help        Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes)
      AUTO_YES=1
      shift
      ;;
    --skip-crons)
      SKIP_CRONS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

say() {
  printf '%s\n' "$*"
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
}

prompt_yes_no() {
  local question="$1"
  local default="${2:-Y}"
  local reply

  if [[ "$AUTO_YES" -eq 1 ]]; then
    return 0
  fi

  if [[ "$default" == "Y" ]]; then
    read -r -p "$question [Y/n]: " reply
    [[ -z "$reply" || "$reply" =~ ^[Yy]$ ]]
  else
    read -r -p "$question [y/N]: " reply
    [[ "$reply" =~ ^[Yy]$ ]]
  fi
}

copy_template_if_missing() {
  local src="$1"
  local dst="$2"

  [[ -f "$src" ]] || return 0
  mkdir -p "$(dirname "$dst")"

  if [[ -f "$dst" ]]; then
    if prompt_yes_no "File exists: $dst. Overwrite with template?" "N"; then
      cp "$src" "$dst"
      say "Updated: $dst"
    else
      say "Kept existing: $dst"
    fi
  else
    cp "$src" "$dst"
    say "Created: $dst"
  fi
}

install_brew_package() {
  local package="$1"
  if ! command -v brew >/dev/null 2>&1; then
    warn "Homebrew not found; install $package manually"
    return 1
  fi
  brew install "$package"
}

install_core_deps() {
  if ! command -v tmux >/dev/null 2>&1; then
    warn "tmux missing"
    prompt_yes_no "Install tmux with Homebrew?" "Y" && install_brew_package tmux || true
  fi
  if ! command -v jq >/dev/null 2>&1; then
    warn "jq missing"
    prompt_yes_no "Install jq with Homebrew?" "Y" && install_brew_package jq || true
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 missing"
    prompt_yes_no "Install python@3.12 with Homebrew?" "Y" && install_brew_package python@3.12 || true
  fi
  if ! command -v gh >/dev/null 2>&1; then
    warn "gh missing"
    prompt_yes_no "Install GitHub CLI with Homebrew?" "Y" && install_brew_package gh || true
  fi
  if ! command -v himalaya >/dev/null 2>&1; then
    warn "himalaya missing"
    prompt_yes_no "Install himalaya with Homebrew?" "Y" && install_brew_package himalaya || true
  fi
  if ! command -v stripe >/dev/null 2>&1; then
    warn "stripe CLI missing"
    if prompt_yes_no "Install Stripe CLI with Homebrew?" "Y"; then
      if command -v brew >/dev/null 2>&1; then
        brew install stripe/stripe-cli/stripe
      else
        warn "Homebrew not found; install stripe manually"
      fi
    fi
  fi

  if ! command -v node >/dev/null 2>&1 || ! node --version | grep -Eq '^v2[2-9]\.'; then
    warn "Node 22+ missing"
    if prompt_yes_no "Install node@22 with Homebrew?" "Y"; then
      install_brew_package node@22 || true
      warn "If node still resolves to an older version, add node@22 to PATH manually"
    fi
  fi

  if command -v npm >/dev/null 2>&1; then
    for package in pnpm openclaw@latest ralphy-cli @openai/codex @anthropic-ai/claude-code vercel; do
      local binary="${package##*/}"
      if [[ "$binary" == "openclaw@latest" ]]; then
        binary="openclaw"
      elif [[ "$binary" == "ralphy-cli" ]]; then
        binary="ralphy"
      elif [[ "$binary" == "codex" ]]; then
        binary="codex"
      elif [[ "$binary" == "claude-code" ]]; then
        binary="claude"
      elif [[ "$binary" == "pnpm" ]]; then
        binary="pnpm"
      fi
      if ! command -v "$binary" >/dev/null 2>&1; then
        warn "$binary missing"
        prompt_yes_no "Install $package with npm -g?" "Y" && npm install -g "$package" || true
      fi
    done
  else
    warn "npm not found; install pnpm, openclaw, ralphy-cli, codex, claude-code, and vercel manually"
  fi
}

setup_integrations() {
  mkdir -p \
    "$HOME/.config/stripe" \
    "$HOME/.config/x-api" \
    "$HOME/.config/elevenlabs" \
    "$HOME/.config/twilio" \
    "$HOME/.config/himalaya" \
    "$HOME/.config/resend" \
    "$HOME/.config/openclaw" \
    "$HOME/.config/youtube" \
    "$HOME/.clawdbot"

  copy_template_if_missing "$ROOT_DIR/templates/integrations/stripe-api-key.env.example" "$HOME/.config/stripe/api_key.env"
  copy_template_if_missing "$ROOT_DIR/templates/integrations/x-api-keys.env.example" "$HOME/.config/x-api/keys.env"
  copy_template_if_missing "$ROOT_DIR/templates/integrations/elevenlabs.env.example" "$HOME/.config/elevenlabs/api_key.env"
  copy_template_if_missing "$ROOT_DIR/templates/integrations/twilio.env.example" "$HOME/.config/twilio/credentials.env"
  copy_template_if_missing "$ROOT_DIR/templates/integrations/himalaya-config.toml.example" "$HOME/.config/himalaya/config.toml"
  copy_template_if_missing "$ROOT_DIR/templates/integrations/resend.env.example" "$HOME/.config/resend/api_key.env"
  copy_template_if_missing "$ROOT_DIR/templates/integrations/youtube-api-key.example" "$HOME/.config/youtube/api_key"
  copy_template_if_missing "$ROOT_DIR/templates/integrations/.secrets.example" "$HOME/.clawdbot/.secrets"
  copy_template_if_missing "$ROOT_DIR/templates/integrations/health-targets.conf.example" "$HOME/.config/openclaw/health-targets.conf"

  chmod 600 "$HOME/.clawdbot/.secrets" 2>/dev/null || true
  chmod 600 "$HOME/.config/himalaya/config.toml" 2>/dev/null || true
  chmod 600 "$HOME/.config/youtube/api_key" 2>/dev/null || true
}

seed_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    mkdir -p "$(dirname "$ENV_FILE")"
    cp "$ROOT_DIR/config/rick.env.example" "$ENV_FILE"
    say "Created: $ENV_FILE"
  fi
}

install_xpost() {
  local src="$ROOT_DIR/bin/xpost"
  local dst_dir="$HOME/.local/bin"
  local dst="$dst_dir/xpost"

  if [[ ! -f "$src" ]]; then
    warn "Bundled xpost not found: $src"
    return 1
  fi

  mkdir -p "$dst_dir"
  cp "$src" "$dst"
  chmod +x "$dst"
  say "Installed xpost to: $dst"

  if [[ ":$PATH:" != *":$dst_dir:"* ]]; then
    if prompt_yes_no "Add $dst_dir to PATH in ~/.zshrc?" "Y"; then
      if ! grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.zshrc" 2>/dev/null; then
        printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"
      fi
      say "Added ~/.local/bin PATH export to ~/.zshrc"
    fi
  fi
}

setup_tmux_socket() {
  mkdir -p "$HOME/.tmux"
  if command -v tmux >/dev/null 2>&1; then
    tmux -S "$HOME/.tmux/sock" start-server || true
    say "tmux stable socket ready: ~/.tmux/sock"
  fi
}

install_crons_step() {
  if [[ "$SKIP_CRONS" -eq 1 ]]; then
    say "Skipped cron installation (--skip-crons)"
    return 0
  fi
  bash "$ROOT_DIR/scripts/install-crons.sh"
}

print_checklist() {
  cat <<'CHECKLIST'

Setup complete. Fill these values before production use:

1. config/rick.env
   - model keys
   - Telegram
   - Stripe/Beehiiv/LinkedIn
   - real domains and file paths
2. config/model-pricing.json
   - verify per-model pricing so token economics reflects reality

3. config/openclaw-session-policy.json
   - keep one main agent active now: rick
   - keep secure DM mode prepared, not enabled
4. config/openclaw-agent-blueprint.json
   - future 4-agent split only; do not activate yet
5. templates/openclaw/memory-flush.prompt.md
   - review the Rick Vault memory-flush instructions
6. OPENCLAW_PROFILE.md
   - follow the single-agent-now / four-agent-later rollout
7. ~/.config/x-api/keys.env
8. ~/.config/himalaya/config.toml
9. ~/.config/openclaw/health-targets.conf
10. ~/.config/youtube/api_key
11. ~/.clawdbot/.secrets
12. ~/rick-vault/control/*.md placeholders

Then run:
  bash scripts/preflight-openclaw.sh
  bash scripts/bootstrap.sh
  bash scripts/doctor.sh
CHECKLIST
}

install_python_deps() {
  if [[ -f "$ROOT_DIR/requirements.txt" ]]; then
    if prompt_yes_no "Install Python dependencies from requirements.txt?" "Y"; then
      python3 -m pip install --user -r "$ROOT_DIR/requirements.txt" || {
        warn "pip install failed; install dependencies manually: pip3 install -r requirements.txt"
      }
    fi
  else
    warn "requirements.txt not found at $ROOT_DIR/requirements.txt"
  fi
}

seed_env_file
install_core_deps
install_python_deps
setup_integrations
install_xpost
setup_tmux_socket
bash "$ROOT_DIR/scripts/bootstrap.sh"
install_crons_step
bash "$ROOT_DIR/scripts/doctor.sh" || true
print_checklist
