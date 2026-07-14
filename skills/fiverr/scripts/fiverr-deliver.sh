#!/usr/bin/env bash
# fiverr-deliver.sh — Stage deliverables for a Fiverr order.
# Usage: fiverr-deliver.sh <order-slug>
set -euo pipefail

DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
FIVERR_DIR="$DATA_ROOT/fiverr"

ORDER_SLUG="${1:-}"
if [[ -z "$ORDER_SLUG" ]]; then
    echo "Usage: fiverr-deliver.sh <order-slug>"
    exit 1
fi

# S3: Validate slug to prevent path traversal
if [[ ! "$ORDER_SLUG" =~ ^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$ ]]; then
    echo "Invalid order slug: $ORDER_SLUG"
    exit 1
fi

ORDER_DIR="$FIVERR_DIR/orders/$ORDER_SLUG"
DELIVERY_DIR="$ORDER_DIR/delivery-package"

if [[ ! -d "$ORDER_DIR" ]]; then
    echo "Order directory not found: $ORDER_DIR"
    exit 1
fi

mkdir -p "$DELIVERY_DIR"

# Collect all deliverable files
echo "=== Staging Delivery for $ORDER_SLUG ==="

# Copy deliverables
if [[ -d "$ORDER_DIR/deliverable" ]]; then
    cp -r "$ORDER_DIR/deliverable/"* "$DELIVERY_DIR/" 2>/dev/null || true
    echo "Copied deliverable files."
fi

# Generate delivery manifest
RELATIVE_ORDER_DIR="~/rick-vault/fiverr/orders/$ORDER_SLUG"
cat > "$DELIVERY_DIR/DELIVERY-MANIFEST.md" << EOF
# Delivery — $ORDER_SLUG

- Staged at: $(date -u +%Y-%m-%dT%H:%M:%S)
- Order directory: $RELATIVE_ORDER_DIR

## Files
$(find "$DELIVERY_DIR" -type f ! -name "DELIVERY-MANIFEST.md" | sort | sed "s|$DATA_ROOT|~/rick-vault|g" | sed 's|^|  - |')

## Notes
- Review all files before submitting to Fiverr
- Ensure deliverables match order requirements
- Message buyer with any clarifications needed
EOF

echo "Delivery staged at: $DELIVERY_DIR"
echo "Manifest: $DELIVERY_DIR/DELIVERY-MANIFEST.md"
echo ""
echo "Files:"
find "$DELIVERY_DIR" -type f | sort | sed 's|^|  |'
