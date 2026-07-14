#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Users/rickthebot/.openclaw/workspace"
RESULT_FILE="$ROOT_DIR/.tmp/sociavault-scan-result.json"

cd "$ROOT_DIR"

if [ -f "$HOME/clawd/config/rick.env" ]; then
  # shellcheck disable=SC1090
  source "$HOME/clawd/config/rick.env"
elif [ -f "$ROOT_DIR/config/rick.env" ]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/config/rick.env"
fi

export PYTHONUNBUFFERED=1

echo "[buyer-intent-cron] SociaVault credits"
bash "$ROOT_DIR/skills/sociavault/scripts/sociavault.sh" credits

echo "[buyer-intent-cron] Running SociaVault enhanced scan"
python3 "$ROOT_DIR/scripts/sociavault-intent-scan.py"

echo "[buyer-intent-cron] Enhanced scan summary"
python3 - "$RESULT_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(f"missing result file: {path}")

data = json.loads(path.read_text())
print(f"posts_scanned={data.get('posts_scanned', 0)}")
print(f"leads_found={data.get('leads_found', 0)}")

for lead in data.get("top_leads", [])[:5]:
    url = lead.get("url") or lead.get("post_url") or ""
    print(f"top_lead score={lead.get('score')} platform={lead.get('platform')} url={url}")
PY

echo "[buyer-intent-cron] Running baseline radar"
(
  cd "$ROOT_DIR/skills/free-ride/jobs"
  python3 buyer-intent-radar.py
)

echo "[buyer-intent-cron] HEARTBEAT_OK"
