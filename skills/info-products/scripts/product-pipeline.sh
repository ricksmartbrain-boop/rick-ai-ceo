#!/usr/bin/env bash
# Info Products pipeline — view products by stage.
#
# Usage:
#   product-pipeline.sh                    # All products
#   product-pipeline.sh --stage idea       # Only ideas
#   product-pipeline.sh --stage draft      # Only drafts
#   product-pipeline.sh --stage live       # Only live products

set -euo pipefail

RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
STAGE="all"
VAULT="$RICK_DATA_ROOT/projects/info-products"

while [[ $# -gt 0 ]]; do
    case $1 in
        --stage) STAGE="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "═══════════════════════════════════════"
echo "  Info Products — Pipeline"
echo "  $(date '+%Y-%m-%d %H:%M')"
echo "═══════════════════════════════════════"
echo ""

echo "📊 Revenue Target: \$40,000/month"
echo ""

# Pipeline stages
echo "📋 Product Pipeline"
echo "───────────────────────────────────────"
echo ""

# Check vault for product items
if [ -f "$VAULT/items.json" ]; then
    ITEMS=$(cat "$VAULT/items.json")
    COUNT=$(echo "$ITEMS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

    if [ "$COUNT" -gt 0 ]; then
        echo "  Found $COUNT product facts in vault"
        echo "$ITEMS" | python3 -c "
import sys, json
items = json.load(sys.stdin)
for item in items:
    if item.get('category') == 'product':
        print(f\"  {item.get('status', '?'):12} | {item['fact']}\")
" 2>/dev/null
    fi
fi

echo ""
echo "📦 Planned Products (from strategy)"
echo "───────────────────────────────────────"

# Hardcoded planned products (updated as they progress)
declare -A PRODUCTS
PRODUCTS=(
    ["AI Agent Builder Course"]="idea|$99-$299|How to build autonomous AI agents with Claude, GPT, Gemini"
    ["B2B Lead Gen Masterclass"]="idea|$99|Partner Connector war stories, lead qualification, closing"
    ["SaaS from Zero Guide"]="idea|$29-$99|Complete guide from idea to first paying customer"
    ["AI CEO Playbook"]="idea|$299|Running your business with AI agents (Rick's own story)"
)

for product in "${!PRODUCTS[@]}"; do
    IFS='|' read -r stage price desc <<< "${PRODUCTS[$product]}"
    if [ "$STAGE" = "all" ] || [ "$STAGE" = "$stage" ]; then
        case $stage in
            idea)     icon="💡" ;;
            outline)  icon="📝" ;;
            draft)    icon="✍️" ;;
            review)   icon="🔍" ;;
            launch)   icon="🚀" ;;
            live)     icon="✅" ;;
            *)        icon="❓" ;;
        esac
        echo "  $icon [$stage] $product"
        echo "     Price: $price"
        echo "     $desc"
        echo ""
    fi
done

echo "───────────────────────────────────────"
echo "  Pipeline: Idea → Outline → Draft → Review → Launch → Live"
echo ""
echo "  Pricing ladder: Free → \$29 → \$99 → \$299 → \$99/mo"
echo "  Target: 2-3 evergreen courses for \$40K/mo"
echo "═══════════════════════════════════════"
