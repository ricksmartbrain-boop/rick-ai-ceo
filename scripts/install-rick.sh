#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_URL="https://github.com/ricksmartbrain-boop/rick-ai-ceo.git"
INSTALL_ROOT="${HOME}/rick-install"
TEST_EMAIL=""
SOURCE_ROOT=""
DRY_RUN=0
FORCE_MODE=""
HELP=0
PYTHON_BIN=""

usage() {
  cat <<'USAGE'
Usage: scripts/install-rick.sh [options]

Install Rick on a fresh macOS machine.

Options:
  --install-dir <dir>   Install root (default: ~/rick-install)
  --repo-url <url>      Override git repo URL
  --source-root <dir>   Copy from a local working tree instead of cloning (testing)
  --test-email <addr>   Smoke-test recipient for the cold-email send
  --reinstall           Re-run install on an existing Rick
  --update-keys         Only update secrets on an existing Rick
  --dry-run             Render config and validate, but skip launchd + email send
  -h, --help            Show this help
USAGE
}

say() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

trim() {
  local value="$1"
  value="${value#${value%%[![:space:]]*}}"
  value="${value%${value##*[![:space:]]}}"
  printf '%s' "$value"
}

slugify() {
  local value="$1"
  value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
  value="$(printf '%s' "$value" | tr -cs 'a-z0-9' '-')"
  value="${value#-}"
  value="${value%-}"
  printf '%s' "$value"
}

sq() {
  local value="${1:-}"
  value="${value//\'/\'\"\'\"\'}"
  printf "'%s'" "$value"
}

write_export() {
  local key="$1"
  local value="${2:-}"
  printf 'export %s=%s\n' "$key" "$(sq "$value")"
}

prompt_text() {
  local current_value="${1:-}"
  local prompt="$2"
  local default_value="${3:-}"
  local input=""

  if [[ -n "$current_value" ]]; then
    printf '%s' "$current_value"
    return 0
  fi

  if [[ -n "$default_value" ]]; then
    read -r -p "$prompt [$default_value]: " input
    input="$(trim "$input")"
    printf '%s' "${input:-$default_value}"
  else
    read -r -p "$prompt: " input
    printf '%s' "$(trim "$input")"
  fi
}

prompt_secret() {
  local current_value="${1:-}"
  local prompt="$2"
  local optional="${3:-0}"
  local input=""

  if [[ -n "$current_value" ]]; then
    printf '%s' "$current_value"
    return 0
  fi

  if [[ "$optional" == "1" ]]; then
    read -r -s -p "$prompt [optional]: " input
  else
    read -r -s -p "$prompt: " input
  fi
  printf '\n' >&2
  printf '%s' "$(trim "$input")"
}

ensure_macos() {
  [[ "$(uname -s)" == "Darwin" ]] || die "Rick install is macOS-only"
}

ensure_brew() {
  if command -v brew >/dev/null 2>&1; then
    eval "$(brew shellenv)"
    return 0
  fi

  say "Homebrew missing — installing it now."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)" 2>/dev/null || true
  fi

  command -v brew >/dev/null 2>&1 || die "Homebrew install finished but brew is still unavailable"
  eval "$(brew shellenv)"
}

brew_formula_installed() {
  brew list --formula "$1" >/dev/null 2>&1
}

brew_cask_installed() {
  brew list --cask "$1" >/dev/null 2>&1
}

ensure_xcode_clt() {
  if xcode-select -p >/dev/null 2>&1; then
    return 0
  fi

  warn "Xcode Command Line Tools missing; opening the installer prompt."
  xcode-select --install >/dev/null 2>&1 || true
  for _ in $(seq 1 60); do
    if xcode-select -p >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  die "Xcode Command Line Tools still missing. Finish the popup, then rerun the installer."
}

ensure_formula() {
  local formula="$1"
  local command_name="${2:-$1}"
  if command -v "$command_name" >/dev/null 2>&1; then
    return 0
  fi
  if ! brew_formula_installed "$formula"; then
    say "Installing $formula"
    brew install "$formula"
  fi
}

ensure_cask() {
  local cask="$1"
  if command -v google-chrome >/dev/null 2>&1 || [[ -d "/Applications/Google Chrome.app" ]]; then
    return 0
  fi
  if ! brew_cask_installed "$cask"; then
    say "Installing $cask"
    brew install --cask "$cask"
  fi
}

ensure_python() {
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.12)"
  else
    if ! brew_formula_installed python@3.12; then
      say "Installing python@3.12"
      brew install python@3.12
    fi
    PYTHON_BIN="$(brew --prefix python@3.12)/bin/python3.12"
  fi

  "$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit(1)
PY
}

ensure_prereqs() {
  ensure_xcode_clt
  ensure_brew
  ensure_python
  ensure_formula git git
  ensure_formula ffmpeg ffmpeg
  ensure_cask google-chrome
}

exists_nonempty_dir() {
  local dir="$1"
  [[ -d "$dir" ]] || return 1
  [[ -n "$(find "$dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]
}

install_slug_from_path() {
  slugify "$(basename "$INSTALL_ROOT")"
}

hostname_slug() {
  local host
  host="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo mac)"
  slugify "$host"
}

load_existing_env() {
  local env_file="$1"
  [[ -f "$env_file" ]] || return 0
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
}

pick_free_port() {
  local start="${1:-9222}"
  local end="${2:-9300}"
  "$PYTHON_BIN" - "$start" "$end" <<'PY'
import socket, sys
start = int(sys.argv[1])
end = int(sys.argv[2])
for port in range(start, end + 1):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

validate_http_json() {
  local name="$1"
  local method="$2"
  local url="$3"
  local headers_json="$4"
  local data="${5:-}"
  local tmp_body tmp_status
  tmp_body="$(mktemp)"
  tmp_status="$(mktemp)"

  local curl_args=(--silent --show-error --output "$tmp_body" --write-out '%{http_code}' -X "$method")
  if [[ -n "$headers_json" ]]; then
    while IFS= read -r header; do
      [[ -n "$header" ]] && curl_args+=(-H "$header")
    done < <("$PYTHON_BIN" - "$headers_json" <<'PY'
import json, sys
for item in json.loads(sys.argv[1]):
    print(item)
PY
)
  fi
  if [[ -n "$data" ]]; then
    curl_args+=(-d "$data")
  fi
  curl_args+=("$url")

  local status
  if ! status="$(curl "${curl_args[@]}" 2>"$tmp_status")"; then
    cat "$tmp_status" >&2 || true
    cat "$tmp_body" >&2 || true
    rm -f "$tmp_body" "$tmp_status"
    die "$name validation failed"
  fi

  if [[ "$status" != 2* ]]; then
    say "--- $name response ---"
    cat "$tmp_body" >&2 || true
    rm -f "$tmp_body" "$tmp_status"
    die "$name validation failed (HTTP $status)"
  fi

  rm -f "$tmp_body" "$tmp_status"
}

validate_openai() {
  validate_http_json \
    "OpenAI" \
    "GET" \
    "https://api.openai.com/v1/models" \
    "$(printf '["Authorization: Bearer %s"]' "$OPENAI_KEY")"
}

validate_anthropic() {
  validate_http_json \
    "Anthropic" \
    "GET" \
    "https://api.anthropic.com/v1/models" \
    "$(printf '["x-api-key: %s", "anthropic-version: 2023-06-01"]' "$ANTHROPIC_KEY")"
}

validate_resend() {
  validate_http_json \
    "Resend" \
    "GET" \
    "https://api.resend.com/domains" \
    "$(printf '["Authorization: Bearer %s"]' "$RESEND_KEY")"
}

validate_elevenlabs() {
  validate_http_json \
    "ElevenLabs" \
    "GET" \
    "https://api.elevenlabs.io/v1/user" \
    "$(printf '["xi-api-key: %s"]' "$ELEVENLABS_KEY")"
}

validate_gmail_app_password() {
  local gmail_user="$1"
  "$PYTHON_BIN" - "$gmail_user" "$GMAIL_APP_PASSWORD" <<'PY'
import smtplib
import sys
user, pw = sys.argv[1], sys.argv[2]
with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
    smtp.ehlo()
    smtp.starttls()
    smtp.ehlo()
    smtp.login(user, pw)
PY
}

validate_memelord() {
  local payload='{"prompt":"install-auth-check","count":1}'
  validate_http_json \
    "Memelord" \
    "POST" \
    "https://www.memelord.com/api/v1/ai-meme" \
    "$(printf '["Authorization: Bearer %s", "Content-Type: application/json"]' "$MEMELORD_KEY")" \
    "$payload"
}

choose_mode() {
  if [[ "$FORCE_MODE" == "reinstall" || "$FORCE_MODE" == "update-keys" ]]; then
    return 0
  fi

  if [[ -f "$ENV_FILE" ]]; then
    say "Rick already exists at: $INSTALL_ROOT"
    while true; do
      read -r -p "Choose [r]einstall, [u]pdate keys, or [e]xit [u]: " choice
      choice="$(trim "${choice:-u}")"
      choice_lc="$(printf '%s' "$choice" | tr '[:upper:]' '[:lower:]')"
      case "$choice_lc" in
        r|reinstall) FORCE_MODE="reinstall"; break ;;
        u|update-keys) FORCE_MODE="update-keys"; break ;;
        e|exit) exit 0 ;;
        *) warn "Pick r, u, or e." ;;
      esac
    done
  else
    FORCE_MODE="install"
  fi
}

ensure_repo_layout() {
  mkdir -p "$INSTALL_ROOT"

  if [[ -d "$INSTALL_ROOT/.git" ]]; then
    return 0
  fi

  if [[ -n "$SOURCE_ROOT" ]]; then
    say "Copying source tree from $SOURCE_ROOT"
    rsync -a \
      --delete \
      --exclude '.git/' \
      --exclude 'node_modules/' \
      --exclude '.tmp/' \
      --exclude 'tmp/' \
      --exclude 'artifacts/' \
      --exclude 'logs/' \
      --exclude '.pytest_cache/' \
      --exclude 'data/' \
      --exclude 'config/rick.env' \
      --exclude 'config/install-state.json' \
      --exclude 'config/launchd/' \
      "$SOURCE_ROOT"/ "$INSTALL_ROOT"/
    return 0
  fi

  if exists_nonempty_dir "$INSTALL_ROOT"; then
    die "Install dir exists and is not empty: $INSTALL_ROOT. Pick --install-dir or clear it first."
  fi

  say "Cloning repo to $INSTALL_ROOT"
  git clone "$REPO_URL" "$INSTALL_ROOT"
}

bootstrap_db() {
  local db_path="$1"
  local workspace_root="$2"
  local data_root="$3"
  RICK_WORKSPACE_ROOT="$workspace_root" RICK_DATA_ROOT="$data_root" RICK_RUNTIME_DB_FILE="$db_path" PYTHONPATH="$workspace_root" "$PYTHON_BIN" - <<'PY'
import os
from runtime.db import connect, init_db, migrate_db
conn = connect()
try:
    init_db(conn)
    migrate_db(conn)
    conn.commit()
finally:
    conn.close()
PY
}

render_plist() {
  local label="$1"
  local script_rel="$2"
  local out_path="$3"
  local workspace_root="$4"
  local data_root="$5"
  local extra_env_json="$6"
  local interval="${7:-}"
  local keepalive="${8:-1}"

  cat > "$out_path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${workspace_root}/${script_rel}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${workspace_root}</string>
  <key>RunAtLoad</key>
  <true/>
EOF

  if [[ -n "$interval" ]]; then
    cat >> "$out_path" <<EOF
  <key>StartInterval</key>
  <integer>${interval}</integer>
EOF
  fi

  if [[ "$keepalive" == "1" ]]; then
    cat >> "$out_path" <<'EOF'
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
EOF
  fi

  cat >> "$out_path" <<EOF
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>${data_root}/logs/${label}.out.log</string>
  <key>StandardErrorPath</key>
  <string>${data_root}/logs/${label}.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>RICK_ENV_FILE</key>
    <string>${data_root}/config/rick.env</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
EOF

  if [[ -n "$extra_env_json" ]]; then
    while IFS= read -r kv; do
      [[ -z "$kv" ]] && continue
      local key value
      key="${kv%%=*}"
      value="${kv#*=}"
      cat >> "$out_path" <<EOF
    <key>${key}</key>
    <string>${value}</string>
EOF
    done < <("$PYTHON_BIN" - "$extra_env_json" <<'PY'
import json, sys
for key, value in json.loads(sys.argv[1]).items():
    print(f"{key}={value}")
PY
)
  fi

  cat >> "$out_path" <<'EOF'
  </dict>
</dict>
</plist>
EOF
}

bootstrap_launch_agent() {
  local plist_path="$1"
  local label="$2"
  local uid
  uid="$(id -u)"
  launchctl bootout "gui/${uid}/${label}" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/${uid}" "$plist_path"
  launchctl enable "gui/${uid}/${label}" >/dev/null 2>&1 || true
  launchctl kickstart -k "gui/${uid}/${label}" >/dev/null 2>&1 || true
}

smoke_heartbeat() {
  local workspace_root="$1"
  local env_file="$2"
  RICK_ENV_FILE="$env_file" RICK_WORKSPACE_ROOT="$workspace_root" "$PYTHON_BIN" "$workspace_root/runtime/runner.py" heartbeat --work-limit 0
}

smoke_digest() {
  local workspace_root="$1"
  local env_file="$2"
  RICK_ENV_FILE="$env_file" RICK_WORKSPACE_ROOT="$workspace_root" "$PYTHON_BIN" "$workspace_root/scripts/rick-activity-digest.py" --dry-run
}

smoke_email() {
  local env_file="$1"
  local recipient="$2"
  "$PYTHON_BIN" - "$env_file" "$recipient" <<'PY'
import json
import os
import sys
import urllib.request
from pathlib import Path

env_file = Path(sys.argv[1])
recipient = sys.argv[2]
values = {}
for line in env_file.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    if line.startswith('export '):
        line = line[7:]
    key, _, raw = line.partition('=')
    value = raw.strip()
    if value.startswith("'") and value.endswith("'"):
        value = value[1:-1].replace("'\"'\"'", "'")
    values[key.strip()] = value

api_key = values.get('RESEND_API_KEY', '')
from_addr = values.get('MEETRICK_FROM_EMAIL', 'Rick <hello@meetrick.ai>')
if not api_key:
    raise SystemExit('RESEND_API_KEY missing')

payload = {
    'from': from_addr,
    'to': [recipient],
    'subject': 'Rick install smoke test',
    'html': '<p>Rick install smoke test.</p>',
    'text': 'Rick install smoke test.'
}
req = urllib.request.Request(
    'https://api.resend.com/emails',
    data=json.dumps(payload).encode('utf-8'),
    headers={
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'User-Agent': 'curl/8.0',
        'Accept': 'application/json',
    },
    method='POST',
)
with urllib.request.urlopen(req, timeout=30) as resp:
    body = json.loads(resp.read().decode('utf-8'))
print(json.dumps({'status': resp.status, 'id': body.get('id', '')}, indent=2))
PY
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      INSTALL_ROOT="${2:-}"
      shift 2
      ;;
    --repo-url)
      REPO_URL="${2:-$REPO_URL}"
      shift 2
      ;;
    --source-root)
      SOURCE_ROOT="${2:-}"
      shift 2
      ;;
    --test-email)
      TEST_EMAIL="${2:-}"
      shift 2
      ;;
    --reinstall)
      FORCE_MODE="reinstall"
      shift
      ;;
    --update-keys)
      FORCE_MODE="update-keys"
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      HELP=1
      shift
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

if [[ "$HELP" == "1" ]]; then
  usage
  exit 0
fi

ensure_macos
ensure_prereqs

INSTALL_ROOT="$(cd "$(dirname "$INSTALL_ROOT")" && pwd)/$(basename "$INSTALL_ROOT")"
HOST_SLUG="$(hostname_slug)"
INSTALL_SLUG="$(install_slug_from_path)"
LABEL_BASE="ai.rick-${HOST_SLUG}-${INSTALL_SLUG}"
CONFIG_DIR="$INSTALL_ROOT/config"
DATA_ROOT="$INSTALL_ROOT/data"
LAUNCHD_DIR="$INSTALL_ROOT/launchd"
ENV_FILE="$CONFIG_DIR/rick.env"
DB_FILE="$DATA_ROOT/runtime/rick-runtime.db"
STATE_FILE="$CONFIG_DIR/install-state.json"
PLIST_HEARTBEAT="$HOME/Library/LaunchAgents/${LABEL_BASE}.heartbeat.plist"
PLIST_DAEMON="$HOME/Library/LaunchAgents/${LABEL_BASE}.daemon.plist"

if [[ -f "$ENV_FILE" ]]; then
  load_existing_env "$ENV_FILE"
fi

choose_mode

if [[ "$FORCE_MODE" == "update-keys" && ! -f "$ENV_FILE" ]]; then
  die "No existing install found at $INSTALL_ROOT"
fi

if [[ "$FORCE_MODE" == "install" ]]; then
  ensure_repo_layout
elif [[ "$FORCE_MODE" == "reinstall" ]]; then
  if [[ ! -d "$INSTALL_ROOT/.git" ]]; then
    ensure_repo_layout
  fi
fi

mkdir -p "$CONFIG_DIR" "$DATA_ROOT/runtime" "$DATA_ROOT/logs" "$DATA_ROOT/control" "$DATA_ROOT/memory" "$LAUNCHD_DIR" "$HOME/Library/LaunchAgents"

OPENAI_KEY="$(prompt_secret "${OPENAI_API_KEY:-}" "OpenAI key")"
ANTHROPIC_KEY="$(prompt_secret "${ANTHROPIC_API_KEY:-}" "Anthropic key")"
RESEND_KEY="$(prompt_secret "${RESEND_API_KEY:-}" "Resend key")"
ELEVENLABS_KEY="$(prompt_secret "${ELEVENLABS_API_KEY:-}" "ElevenLabs key" 1)"
MEMELORD_KEY="$(prompt_secret "${MEMELORD_API_KEY:-}" "Memelord key" 1)"
GMAIL_APP_PASSWORD="$(prompt_secret "${GMAIL_APP_PASSWORD:-}" "Gmail app password" 1)"
if [[ -n "$GMAIL_APP_PASSWORD" || -n "${GMAIL_IMAP_USER:-}" ]]; then
  GMAIL_IMAP_USER="$(prompt_text "${GMAIL_IMAP_USER:-}" "Gmail user (for app-password validation)" "hello@meetrick.ai")"
else
  GMAIL_IMAP_USER=""
fi

if [[ -z "$TEST_EMAIL" ]]; then
  read -r -p "Smoke-test email address [optional]: " TEST_EMAIL
  TEST_EMAIL="$(trim "$TEST_EMAIL")"
fi

OPENAI_API_KEY="$OPENAI_KEY"
ANTHROPIC_API_KEY="$ANTHROPIC_KEY"
RESEND_API_KEY="$RESEND_KEY"
ELEVENLABS_API_KEY="$ELEVENLABS_KEY"
MEMELORD_API_KEY="$MEMELORD_KEY"
GMAIL_APP_PASSWORD="$GMAIL_APP_PASSWORD"
export OPENAI_API_KEY ANTHROPIC_API_KEY RESEND_API_KEY ELEVENLABS_API_KEY MEMELORD_API_KEY GMAIL_APP_PASSWORD GMAIL_IMAP_USER

CDP_PORT="${RICK_CDP_PORT:-}"
if [[ -z "$CDP_PORT" ]]; then
  CDP_PORT="$(pick_free_port 9222 9300)"
fi

RICK_INSTALL_ROOT="$INSTALL_ROOT"
RICK_WORKSPACE_ROOT="$INSTALL_ROOT"
RICK_DATA_ROOT="$DATA_ROOT"
RICK_RUNTIME_DB_FILE="$DB_FILE"
RICK_ENV_FILE="$ENV_FILE"
RICK_TMUX_SOCKET_PATH="$DATA_ROOT/tmux.sock"
RICK_CDP_PORT="$CDP_PORT"
RICK_LINKEDIN_CDP_PORT="$CDP_PORT"
RICK_INSTAGRAM_CDP_PORT="$CDP_PORT"
RICK_THREADS_CDP_PORT="$CDP_PORT"
RICK_REDDIT_CDP_PORT="$CDP_PORT"
RICK_INSTALL_LABEL_BASE="$LABEL_BASE"

cat > "$ENV_FILE" <<EOF
# Rick install env — generated by scripts/install-rick.sh
$(write_export RICK_INSTALL_ROOT "$RICK_INSTALL_ROOT")
$(write_export RICK_WORKSPACE_ROOT "$RICK_WORKSPACE_ROOT")
$(write_export RICK_DATA_ROOT "$RICK_DATA_ROOT")
$(write_export RICK_RUNTIME_DB_FILE "$RICK_RUNTIME_DB_FILE")
$(write_export RICK_ENV_FILE "$RICK_ENV_FILE")
$(write_export RICK_TMUX_SOCKET_PATH "$RICK_TMUX_SOCKET_PATH")
$(write_export RICK_CDP_PORT "$RICK_CDP_PORT")
$(write_export RICK_LINKEDIN_CDP_PORT "$RICK_LINKEDIN_CDP_PORT")
$(write_export RICK_INSTAGRAM_CDP_PORT "$RICK_INSTAGRAM_CDP_PORT")
$(write_export RICK_THREADS_CDP_PORT "$RICK_THREADS_CDP_PORT")
$(write_export RICK_REDDIT_CDP_PORT "$RICK_REDDIT_CDP_PORT")
$(write_export RICK_INSTALL_LABEL_BASE "$RICK_INSTALL_LABEL_BASE")
$(write_export MEETRICK_FROM_EMAIL "Rick <hello@meetrick.ai>")
$(write_export RICK_DAEMON_INTERVAL_SECONDS "120")
$(write_export RICK_DAEMON_WORK_LIMIT "3")
$(write_export RICK_OPENCLAW_SECURE_DM_MODE "prepared")
$(write_export OPENAI_API_KEY "$OPENAI_API_KEY")
$(write_export ANTHROPIC_API_KEY "$ANTHROPIC_API_KEY")
$(write_export RESEND_API_KEY "$RESEND_API_KEY")
$(write_export ELEVENLABS_API_KEY "$ELEVENLABS_API_KEY")
$(write_export MEMELORD_API_KEY "$MEMELORD_API_KEY")
$(write_export GMAIL_APP_PASSWORD "$GMAIL_APP_PASSWORD")
$(write_export GMAIL_IMAP_USER "$GMAIL_IMAP_USER")
EOF
chmod 600 "$ENV_FILE"

if [[ -f "$STATE_FILE" ]]; then
  :
else
  cat > "$STATE_FILE" <<EOF
{
  "install_root": "$INSTALL_ROOT",
  "hostname": "$HOST_SLUG",
  "label_base": "$LABEL_BASE",
  "cdp_port": $CDP_PORT,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
fi

say "Install root: $INSTALL_ROOT"
say "Data root:    $DATA_ROOT"
say "Labels:       $LABEL_BASE.*"
say "CDP port:     $CDP_PORT"

if [[ "$FORCE_MODE" != "update-keys" ]]; then
  bootstrap_db "$DB_FILE" "$INSTALL_ROOT" "$DATA_ROOT"
fi

say "Validating keys..."
validate_openai
validate_anthropic
validate_resend
if [[ -n "$ELEVENLABS_KEY" ]]; then
  validate_elevenlabs
fi
if [[ -n "$GMAIL_APP_PASSWORD" ]]; then
  validate_gmail_app_password "$GMAIL_IMAP_USER"
fi
if [[ -n "$MEMELORD_KEY" ]]; then
  validate_memelord
fi

render_plist "$LABEL_BASE.heartbeat" "scripts/run-heartbeat.sh" "$PLIST_HEARTBEAT" "$INSTALL_ROOT" "$DATA_ROOT" "{}" 1800 0
render_plist "$LABEL_BASE.daemon" "scripts/run-daemon.sh" "$PLIST_DAEMON" "$INSTALL_ROOT" "$DATA_ROOT" "{}" "" 1
chmod 644 "$PLIST_HEARTBEAT" "$PLIST_DAEMON"

if [[ "$DRY_RUN" == "1" ]]; then
  say "DRY-RUN: wrote env + plists, skipped launchd + smoke tests."
else
  bootstrap_launch_agent "$PLIST_HEARTBEAT" "$LABEL_BASE.heartbeat"
  bootstrap_launch_agent "$PLIST_DAEMON" "$LABEL_BASE.daemon"
fi

say "Heartbeat smoke test:"
if smoke_heartbeat "$INSTALL_ROOT" "$ENV_FILE" >/tmp/rick-install-heartbeat.out 2>/tmp/rick-install-heartbeat.err; then
  sed -n '1,40p' /tmp/rick-install-heartbeat.out
else
  cat /tmp/rick-install-heartbeat.err >&2 || true
  die "Heartbeat smoke test failed"
fi
rm -f /tmp/rick-install-heartbeat.out /tmp/rick-install-heartbeat.err

say "Digest smoke test:"
if smoke_digest "$INSTALL_ROOT" "$ENV_FILE" >/tmp/rick-install-digest.out 2>/tmp/rick-install-digest.err; then
  sed -n '1,40p' /tmp/rick-install-digest.out
else
  cat /tmp/rick-install-digest.err >&2 || true
  die "Digest smoke test failed"
fi
rm -f /tmp/rick-install-digest.out /tmp/rick-install-digest.err

if [[ -n "$TEST_EMAIL" ]]; then
  say "Cold-email smoke test to $TEST_EMAIL:"
  smoke_email "$ENV_FILE" "$TEST_EMAIL"
else
  warn "No smoke-test email provided; skipped cold-email send."
fi

cat <<EOF
Rick installed. Run /status to see your daemon.
Workspace: $INSTALL_ROOT
Env:       $ENV_FILE
Logs:      $DATA_ROOT/logs/${LABEL_BASE}.out.log
           $DATA_ROOT/logs/${LABEL_BASE}.err.log
EOF
