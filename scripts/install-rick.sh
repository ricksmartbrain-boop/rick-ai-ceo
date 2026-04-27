#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_URL="https://github.com/ricksmartbrain-boop/rick-ai-ceo.git"
TENANT_ID=""
TEST_EMAIL=""
DRY_RUN=0
HELP=0
PYTHON_BIN=""
SOURCE_ROOT="${RICK_INSTALL_SOURCE_ROOT:-}"

usage() {
  cat <<'USAGE'
Usage: scripts/install-rick.sh [options]

Scaffold a new tenant install of Rick on macOS.

Options:
  --tenant-id <id>     Tenant slug (required unless prompted)
  --test-email <addr>  Smoke-test recipient for the cold-email path
  --repo-url <url>     Override repo origin
  --source-root <dir>  Copy from a local working tree instead of cloning (testing only)
  --dry-run            Render files and verify flow, but do not bootstrap launchd or send email
  -h, --help           Show this help
USAGE
}

say() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

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

trim() {
  local value="$1"
  value="${value#${value%%[![:space:]]*}}"
  value="${value%${value##*[![:space:]]}}"
  printf '%s' "$value"
}

normalize_tenant_id() {
  local raw="$1"
  raw="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  raw="$(printf '%s' "$raw" | tr -cs 'a-z0-9' '-')"
  raw="${raw#-}"
  raw="${raw%-}"
  printf '%s' "$raw"
}

require_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required"
}

prompt_value() {
  local var_name="$1"
  local prompt_text="$2"
  local default_value="${3:-}"
  local current_value="${!var_name:-}"
  local input=""

  if [[ -n "$current_value" ]]; then
    printf '%s' "$current_value"
    return 0
  fi

  if [[ -n "$default_value" ]]; then
    read -r -p "$prompt_text [$default_value]: " input
    input="$(trim "$input")"
    printf '%s' "${input:-$default_value}"
  else
    read -r -p "$prompt_text: " input
    printf '%s' "$(trim "$input")"
  fi
}

prompt_secret() {
  local var_name="$1"
  local prompt_text="$2"
  local optional="${3:-0}"
  local current_value="${!var_name:-}"
  local input=""

  if [[ -n "$current_value" ]]; then
    printf '%s' "$current_value"
    return 0
  fi

  if [[ "$optional" == "1" ]]; then
    read -r -s -p "$prompt_text [optional]: " input
  else
    read -r -s -p "$prompt_text: " input
  fi
  printf '\n' >&2
  printf '%s' "$(trim "$input")"
}

check_macos() {
  [[ "$(uname -s)" == "Darwin" ]] || die "Rick install is macOS-only"
}

check_python() {
  local version
  PYTHON_BIN="$(command -v python3.12 || command -v python3 || true)"
  [[ -n "$PYTHON_BIN" ]] || die "Python 3.12+ is required"
  version="$("$PYTHON_BIN" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
PY
)"
  if ! "$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit(1)
PY
  then
    die "Python 3.12+ is required (found ${version})"
  fi
  say "Python: ${version}"
}

check_chrome() {
  if command -v google-chrome >/dev/null 2>&1 || command -v chrome >/dev/null 2>&1 || [[ -d "/Applications/Google Chrome.app" ]]; then
    return 0
  fi
  die "Chrome is required for CDP (install Google Chrome)"
}

check_ffmpeg() { require_cmd ffmpeg; }
check_git() { require_cmd git; }

bootstrap_db() {
  local db_path="$1"
  local workspace_root="$2"
  RICK_RUNTIME_DB_FILE="$db_path" PYTHONPATH="$workspace_root" "$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path
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
  local interval="${6:-}"
  local keepalive="${7:-1}"

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
  if launchctl print "gui/${uid}/${label}" >/dev/null 2>&1; then
    launchctl bootout "gui/${uid}/${label}" >/dev/null 2>&1 || true
  fi
  launchctl bootstrap "gui/${uid}" "$plist_path"
  launchctl enable "gui/${uid}/${label}" >/dev/null 2>&1 || true
  launchctl kickstart -k "gui/${uid}/${label}" >/dev/null 2>&1 || true
}

smoke_heartbeat() {
  local workspace_root="$1"
  local env_file="$2"
  RICK_ENV_FILE="$env_file" RICK_WORKSPACE_ROOT="$workspace_root" "$PYTHON_BIN" "$workspace_root/runtime/runner.py" heartbeat --work-limit 0
}

smoke_email() {
  local workspace_root="$1"
  local env_file="$2"
  local to_addr="$3"
  local tenant_id="$4"
  RICK_ENV_FILE="$env_file" PYTHONPATH="$workspace_root" "$PYTHON_BIN" - <<'PY'
import json
import os
import urllib.request
from pathlib import Path

workspace_root = Path(os.environ["WORKSPACE_ROOT"])
env_file = Path(os.environ["ENV_FILE"])
recipient = os.environ["TEST_EMAIL"]
tenant_id = os.environ["TENANT_ID"]

values = {}
for line in env_file.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    if line.startswith("export "):
        line = line[7:]
    key, _, raw_value = line.partition("=")
    value = raw_value.strip()
    if value.startswith("'") and value.endswith("'"):
        value = value[1:-1].replace("'\"'\"'", "'")
    values[key.strip()] = value

api_key = values.get("RESEND_API_KEY") or os.environ.get("RESEND_API_KEY", "")
if not api_key:
    raise SystemExit("RESEND_API_KEY missing")

payload = {
    "from": "Rick <rick@meetrick.ai>",
    "to": [recipient],
    "subject": f"Rick install smoke test ({tenant_id})",
    "html": f"<p>Smoke test for tenant <strong>{tenant_id}</strong>.</p>",
    "text": f"Smoke test for tenant {tenant_id}.",
}
req = urllib.request.Request(
    "https://api.resend.com/emails",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=30) as resp:
    body = json.loads(resp.read().decode("utf-8"))
print(json.dumps({"status": resp.status, "id": body.get("id", "")}, indent=2))
PY
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenant-id)
      TENANT_ID="${2:-}"
      shift 2
      ;;
    --test-email)
      TEST_EMAIL="${2:-}"
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

check_macos
check_python
check_ffmpeg
check_git
check_chrome

TENANT_ID_RAW="${TENANT_ID:-}"
if [[ -z "$TENANT_ID_RAW" ]]; then
  read -r -p "Tenant id (slug): " TENANT_ID_RAW
fi
TENANT_ID="$(normalize_tenant_id "$(trim "$TENANT_ID_RAW")")"
[[ -n "$TENANT_ID" ]] || die "Tenant id is required"

OPENAI_KEY="$(prompt_secret OPENAI_API_KEY "OpenAI key")"
ANTHROPIC_KEY="$(prompt_secret ANTHROPIC_API_KEY "Anthropic key")"
RESEND_KEY="$(prompt_secret RESEND_API_KEY "Resend key")"
ELEVENLABS_KEY="$(prompt_secret ELEVENLABS_API_KEY "ElevenLabs key" 1)"
MEMELORD_KEY="$(prompt_secret MEMELORD_API_KEY "Memelord key" 1)"

if [[ -z "$TEST_EMAIL" ]]; then
  read -r -p "Smoke test email address [optional]: " TEST_EMAIL
  TEST_EMAIL="$(trim "$TEST_EMAIL")"
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
CLONE_DIR="$HOME/clawd-rick-${TIMESTAMP}"
TENANT_CONFIG_DIR="$HOME/.rick-${TENANT_ID}"
TENANT_DATA_ROOT="$HOME/rick-vault-${TENANT_ID}"
TENANT_DB="$TENANT_DATA_ROOT/db/rick.db"
ENV_TEMPLATE_SRC="$ROOT_DIR/templates/install/rick.env.template"
ENV_TEMPLATE_DST="$TENANT_CONFIG_DIR/rick.env.template"
ENV_FILE="$TENANT_CONFIG_DIR/rick.env"
LAUNCHD_DIR="$HOME/Library/LaunchAgents"
PLIST_DIR="$TENANT_CONFIG_DIR/launchd"

mkdir -p "$TENANT_CONFIG_DIR" "$TENANT_DATA_ROOT/db" "$TENANT_DATA_ROOT/logs" "$TENANT_DATA_ROOT/control" "$TENANT_DATA_ROOT/memory" "$LAUNCHD_DIR" "$PLIST_DIR"

if [[ ! -f "$ENV_TEMPLATE_SRC" ]]; then
  die "Missing env template: $ENV_TEMPLATE_SRC"
fi
cp "$ENV_TEMPLATE_SRC" "$ENV_TEMPLATE_DST"
chmod 600 "$ENV_TEMPLATE_DST"

{
  write_export RICK_TENANT_ID "$TENANT_ID"
  write_export RICK_WORKSPACE_ROOT "$CLONE_DIR"
  write_export RICK_DATA_ROOT "$TENANT_DATA_ROOT"
  write_export RICK_RUNTIME_DB_FILE "$TENANT_DB"
  write_export RICK_ENV_FILE "$ENV_FILE"
  write_export OPENAI_API_KEY "$OPENAI_KEY"
  write_export ANTHROPIC_API_KEY "$ANTHROPIC_KEY"
  write_export RESEND_API_KEY "$RESEND_KEY"
  write_export ELEVENLABS_API_KEY "$ELEVENLABS_KEY"
  write_export MEMELORD_API_KEY "$MEMELORD_KEY"
  write_export RICK_DAEMON_INTERVAL_SECONDS "120"
  write_export RICK_DAEMON_WORK_LIMIT "3"
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"

say "Tenant config: $TENANT_CONFIG_DIR"
say "Tenant data:   $TENANT_DATA_ROOT"
say "Tenant db:     $TENANT_DB"

if [[ -e "$CLONE_DIR" ]]; then
  die "Clone target already exists: $CLONE_DIR"
fi

if [[ -n "$SOURCE_ROOT" ]]; then
  say "Copying source tree from $SOURCE_ROOT to $CLONE_DIR"
  mkdir -p "$CLONE_DIR"
  rsync -a --delete \
    --exclude '.git/' \
    --exclude 'node_modules/' \
    --exclude '.tmp/' \
    --exclude 'tmp/' \
    --exclude 'artifacts/' \
    --exclude 'logs/' \
    --exclude '.pytest_cache/' \
    "$SOURCE_ROOT"/ "$CLONE_DIR"/
else
  say "Cloning repo to $CLONE_DIR"
  git clone "$REPO_URL" "$CLONE_DIR"
fi

bootstrap_db "$TENANT_DB" "$CLONE_DIR"

HEARTBEAT_LABEL="ai.rick-${TENANT_ID}.heartbeat"
DAEMON_LABEL="ai.rick-${TENANT_ID}.daemon"
TELEGRAM_LABEL="ai.rick-${TENANT_ID}.telegram-bridge"

HEARTBEAT_PLIST="$LAUNCHD_DIR/${HEARTBEAT_LABEL}.plist"
DAEMON_PLIST="$LAUNCHD_DIR/${DAEMON_LABEL}.plist"
TELEGRAM_PLIST="$LAUNCHD_DIR/${TELEGRAM_LABEL}.plist"

render_plist "$HEARTBEAT_LABEL" "scripts/run-heartbeat.sh" "$HEARTBEAT_PLIST" "$CLONE_DIR" "$TENANT_DATA_ROOT" 1800 0
render_plist "$DAEMON_LABEL" "scripts/run-daemon.sh" "$DAEMON_PLIST" "$CLONE_DIR" "$TENANT_DATA_ROOT" "" 1
render_plist "$TELEGRAM_LABEL" "scripts/run-telegram-bridge.sh" "$TELEGRAM_PLIST" "$CLONE_DIR" "$TENANT_DATA_ROOT" "" 1
chmod 644 "$HEARTBEAT_PLIST" "$DAEMON_PLIST" "$TELEGRAM_PLIST"

if [[ "$DRY_RUN" == "1" ]]; then
  say "DRY-RUN: rendered launchd plists but skipped bootstrap/kickstart."
else
  bootstrap_launch_agent "$HEARTBEAT_PLIST" "$HEARTBEAT_LABEL"
  bootstrap_launch_agent "$DAEMON_PLIST" "$DAEMON_LABEL"
  bootstrap_launch_agent "$TELEGRAM_PLIST" "$TELEGRAM_LABEL"
fi

say "Heartbeat smoke test:"
  if smoke_heartbeat "$CLONE_DIR" "$ENV_FILE" >/tmp/rick-install-heartbeat.out 2>/tmp/rick-install-heartbeat.err; then
  sed -n '1,40p' /tmp/rick-install-heartbeat.out
else
  cat /tmp/rick-install-heartbeat.err >&2
  die "Heartbeat smoke test failed"
fi
rm -f /tmp/rick-install-heartbeat.out /tmp/rick-install-heartbeat.err

if [[ -n "$TEST_EMAIL" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    say "Cold-email smoke path: would send to $TEST_EMAIL"
  else
    say "Cold-email smoke test to $TEST_EMAIL:"
    WORKSPACE_ROOT="$CLONE_DIR" ENV_FILE="$ENV_FILE" TEST_EMAIL="$TEST_EMAIL" TENANT_ID="$TENANT_ID" smoke_email "$CLONE_DIR" "$ENV_FILE" "$TEST_EMAIL" "$TENANT_ID"
  fi
else
  warn "No smoke-test email provided; email path not exercised."
fi

cat <<EOF
Rick installed. Run /status to verify.
Tenant id: $TENANT_ID
Workspace:  $CLONE_DIR
EOF
