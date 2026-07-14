#!/usr/bin/env bash
# Initialize Rick's durable workspace.
# Safe to run multiple times -- only creates what's missing.

set -euo pipefail

RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
VAULT="$RICK_DATA_ROOT"
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEMPLATE_ROOT="${RICK_WORKSPACE_ROOT:-$SCRIPT_ROOT}"

if [ ! -d "$TEMPLATE_ROOT/templates/vault" ]; then
    TEMPLATE_ROOT="$SCRIPT_ROOT"
fi

echo "Initializing Rick's vault at $VAULT..."

copy_tree_if_missing() {
    local src="$1"
    local dst="$2"

    [ -d "$src" ] || return 0

    mkdir -p "$dst"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --ignore-existing "$src/" "$dst/"
    else
        cp -R -n "$src/." "$dst/" 2>/dev/null || cp -R "$src/." "$dst/"
    fi
}

# Core PARA directories
dirs=(
    "$VAULT/projects/partner-connector"
    "$VAULT/projects/404-agency"
    "$VAULT/projects/personal-brand"
    "$VAULT/projects/info-products"
    "$VAULT/projects/lingualive"
    "$VAULT/areas/people"
    "$VAULT/areas/companies"
    "$VAULT/areas/operations"
    "$VAULT/resources"
    "$VAULT/archives"
    "$VAULT/revenue"
    "$VAULT/okrs"
    "$VAULT/decisions"
    "$VAULT/weekly-reviews"
    "$VAULT/reflections/daily"
    "$VAULT/reflections/weekly"
    "$VAULT/dashboards"
    "$VAULT/scorecards"
    "$VAULT/control/briefings"
    "$VAULT/control/morning-briefs"
    "$VAULT/operations"
    "$VAULT/memory"
    "$VAULT/content/newsletters/drafts"
    "$VAULT/content/newsletters/archive"
    "$VAULT/content/social"
    "$VAULT/content/launches"
    "$VAULT/content/product-ideas"
    "$VAULT/customers"
    "$VAULT/mailbox/templates"
    "$VAULT/mailbox/sequences"
    "$VAULT/mailbox/outbox"
    "$VAULT/mailbox/triage"
    "$VAULT/.obsidian"
)

for dir in "${dirs[@]}"; do
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        echo "  Created $dir"
    fi
done

copy_tree_if_missing "$TEMPLATE_ROOT/templates/vault" "$VAULT"

# Ensure Obsidian config
if [ ! -f "$VAULT/.obsidian/community-plugins.json" ]; then
    echo '["dataview"]' > "$VAULT/.obsidian/community-plugins.json"
    echo "  Created Obsidian plugin config"
fi

# Ensure items.json for each project
for project in partner-connector 404-agency personal-brand info-products lingualive; do
    items="$VAULT/projects/$project/items.json"
    if [ ! -f "$items" ]; then
        echo "[]" > "$items"
        echo "  Created $items"
    fi
done

echo ""
echo "Rick's vault initialized at $VAULT"
echo "   Projects: $(ls -1 "$VAULT/projects/" | wc -l | tr -d ' ')"
echo "   Ready for operation."
