#!/usr/bin/env bash
# Pull 404 Model Agency metrics via SociaVault API.
#
# Usage:
#   agency-metrics.sh              # Both models
#   agency-metrics.sh --model cat  # Cat only
#   agency-metrics.sh --model luna # Luna only

set -euo pipefail

# Load API key from telegram-sync .env
ENV_FILE="$HOME/telegram-sync/.env"
if [ -f "$ENV_FILE" ]; then
    SOCIAVAULT_API_KEY=$(grep SOCIAVAULT_API_KEY "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'" | tr -d ' ')
else
    echo "Warning: No .env file at $ENV_FILE"
    SOCIAVAULT_API_KEY="${SOCIAVAULT_API_KEY:-}"
fi

if [ -z "$SOCIAVAULT_API_KEY" ]; then
    echo "Error: SOCIAVAULT_API_KEY not found. Set it in $ENV_FILE"
    exit 1
fi

API_BASE="https://api.sociavault.com/v1/scrape"
MODEL="all"

while [[ $# -gt 0 ]]; do
    case $1 in
        --model) MODEL="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

fetch_instagram() {
    local handle=$1
    local name=$2

    echo "Instagram: @$handle ($name)"
    response=$(curl -sf "$API_BASE/instagram/profile?handle=$handle" \
        -H "X-API-Key: $SOCIAVAULT_API_KEY" 2>/dev/null || echo '{"error":"request failed"}')

    if echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'   Followers: {d.get(\"followers\", \"N/A\")}')" 2>/dev/null; then
        echo "$response" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'   Posts: {d.get(\"posts\", \"N/A\")}')
print(f'   Following: {d.get(\"following\", \"N/A\")}')
" 2>/dev/null
    else
        echo "   Warning: Could not fetch metrics"
        echo "   Raw: $response"
    fi
    echo ""
}

echo "======================================="
echo "  404 Model Agency -- Metrics Report"
echo "  $(date '+%Y-%m-%d %H:%M')"
echo "======================================="
echo ""

if [ "$MODEL" = "all" ] || [ "$MODEL" = "cat" ]; then
    fetch_instagram "_catalina_montes" "Cat"
fi

if [ "$MODEL" = "all" ] || [ "$MODEL" = "luna" ]; then
    fetch_instagram "_luna_solano_" "Luna"
fi

echo "---------------------------------------"
echo "Credits used: $( [ "$MODEL" = "all" ] && echo "2" || echo "1" ) (SociaVault)"
