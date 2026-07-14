#!/usr/bin/env bash
# upwork-deliver.sh — Stage deliverables for an Upwork contract.
# Usage: upwork-deliver.sh <contract-slug>
set -euo pipefail

DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
UPWORK_DIR="$DATA_ROOT/upwork"

CONTRACT_SLUG="${1:-}"
if [[ -z "$CONTRACT_SLUG" ]]; then
    echo "Usage: upwork-deliver.sh <contract-slug>"
    exit 1
fi

# Validate slug to prevent path traversal
if [[ ! "$CONTRACT_SLUG" =~ ^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$ ]]; then
    echo "Invalid contract slug: $CONTRACT_SLUG"
    exit 1
fi

CONTRACT_DIR="$UPWORK_DIR/contracts/$CONTRACT_SLUG"
DELIVERY_DIR="$CONTRACT_DIR/delivery-package"

if [[ ! -d "$CONTRACT_DIR" ]]; then
    echo "Contract directory not found: $CONTRACT_DIR"
    exit 1
fi

mkdir -p "$DELIVERY_DIR"

echo "=== Staging Delivery for $CONTRACT_SLUG ==="

# Copy deliverables
if [[ -d "$CONTRACT_DIR/deliverable" ]]; then
    cp -r "$CONTRACT_DIR/deliverable/"* "$DELIVERY_DIR/" 2>/dev/null || true
    echo "Copied deliverable files."
fi

# Generate delivery manifest
RELATIVE_DIR="~/rick-vault/upwork/contracts/$CONTRACT_SLUG"
cat > "$DELIVERY_DIR/DELIVERY-MANIFEST.md" << EOF
# Delivery — $CONTRACT_SLUG

- Staged at: $(date -u +%Y-%m-%dT%H:%M:%S)
- Contract directory: $RELATIVE_DIR

## Files
$(find "$DELIVERY_DIR" -type f ! -name "DELIVERY-MANIFEST.md" | sort | sed "s|$DATA_ROOT|~/rick-vault|g" | sed 's|^|  - |')

## Notes
- Review all files before submitting to Upwork
- Ensure deliverables match contract requirements
- Submit via Upwork milestone submission
EOF

echo "Delivery staged at: $DELIVERY_DIR"
echo "Manifest: $DELIVERY_DIR/DELIVERY-MANIFEST.md"
echo ""
echo "Files:"
find "$DELIVERY_DIR" -type f | sort | sed 's|^|  |'
