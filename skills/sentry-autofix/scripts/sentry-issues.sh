#!/usr/bin/env bash
set -euo pipefail

API_BASE="${SENTRY_API_BASE:-https://sentry.io/api/0}"
ORG="${SENTRY_ORG:-}"
PROJECT="${SENTRY_PROJECT:-}"
TOKEN="${SENTRY_AUTH_TOKEN:-}"
SEVERITY=""
ISSUE_ID=""
ACTION="list"

usage() {
  cat <<EOF
Usage: sentry-issues.sh [OPTIONS]

Options:
  --list                 List recent issues (default)
  --severity <level>     Filter by level (error, warning, fatal)
  --issue <id>           Fetch a specific issue
  -h, --help             Show help
EOF
  exit 0
}

require_env() {
  if [[ -z "$TOKEN" || -z "$ORG" || -z "$PROJECT" ]]; then
    echo "Error: SENTRY_AUTH_TOKEN, SENTRY_ORG, and SENTRY_PROJECT are required." >&2
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list)
      ACTION="list"
      shift
      ;;
    --severity)
      SEVERITY="$2"
      shift 2
      ;;
    --issue)
      ACTION="issue"
      ISSUE_ID="$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      ;;
  esac
done

require_env

auth_header=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

if [[ "$ACTION" == "issue" ]]; then
  if [[ -z "$ISSUE_ID" ]]; then
    echo "Error: --issue requires an id" >&2
    exit 1
  fi
  curl -s "${auth_header[@]}" "$API_BASE/issues/$ISSUE_ID/" | jq .
  exit 0
fi

query=""
if [[ -n "$SEVERITY" ]]; then
  query="?query=level:${SEVERITY}"
fi

curl -s "${auth_header[@]}" "$API_BASE/projects/$ORG/$PROJECT/issues/${query}" | jq -r '
  .[]? |
  "- [\(.level // "unknown")] #\(.id) \(.title // "untitled") (count=\(.count // 0), users=\(.userCount // 0))"
'
