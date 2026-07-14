#!/usr/bin/env bash
# fiverr-orders.sh — Show Fiverr order pipeline status.
# Usage: fiverr-orders.sh [--active|--all]
set -euo pipefail

DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
DB_PATH="${RICK_DB_PATH:-$DATA_ROOT/rick.db}"
FIVERR_DIR="$DATA_ROOT/fiverr"

FILTER="active"
[[ "${1:-}" == "--all" ]] && FILTER="all"

if [[ ! -f "$DB_PATH" ]]; then
    echo "Database not found at $DB_PATH"
    exit 1
fi

echo "=== Fiverr Order Pipeline ==="
echo ""

if [[ "$FILTER" == "all" ]]; then
    sqlite3 -header -column "$DB_PATH" \
        "SELECT id, title, status, stage, priority, created_at
         FROM workflows
         WHERE kind = 'fiverr_order'
         ORDER BY priority ASC, created_at DESC
         LIMIT 20"
else
    sqlite3 -header -column "$DB_PATH" \
        "SELECT id, title, status, stage, priority, created_at
         FROM workflows
         WHERE kind = 'fiverr_order'
           AND status IN ('queued', 'active', 'blocked')
         ORDER BY priority ASC, created_at DESC
         LIMIT 20"
fi

echo ""
echo "=== Active Jobs ==="
sqlite3 -header -column "$DB_PATH" \
    "SELECT j.id, j.step_name, j.status, j.lane, w.title
     FROM jobs j
     JOIN workflows w ON w.id = j.workflow_id
     WHERE w.kind = 'fiverr_order'
       AND j.status IN ('queued', 'running', 'blocked')
     ORDER BY j.step_index ASC
     LIMIT 20"

# Show orders on disk
echo ""
echo "=== Order Directories ==="
if [[ -d "$FIVERR_DIR/orders" ]]; then
    ls -1 "$FIVERR_DIR/orders" 2>/dev/null || echo "(none)"
else
    echo "(none)"
fi
