#!/usr/bin/env bash
set -euo pipefail

OWNER=""
AREA=""
REQUEST=""
IMPACT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --owner) OWNER="$2"; shift 2 ;;
    --area) AREA="$2"; shift 2 ;;
    --request) REQUEST="$2"; shift 2 ;;
    --impact) IMPACT="$2"; shift 2 ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

export RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
FILE="$RICK_DATA_ROOT/control/approvals.md"
mkdir -p "$(dirname "$FILE")"

if [[ ! -f "$FILE" ]]; then
  cat <<'EOF' > "$FILE"
# Approvals

| Date | Status | Owner | Area | Request | Impact |
|------|--------|-------|------|---------|--------|
EOF
fi

echo "| $(date '+%Y-%m-%d') | open | ${OWNER:-vlad} | ${AREA:-general} | ${REQUEST:-missing request} | ${IMPACT:-unspecified} |" >> "$FILE"
echo "Approval logged to $FILE"
