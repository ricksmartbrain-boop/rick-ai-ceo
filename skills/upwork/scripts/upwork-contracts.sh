#!/usr/bin/env bash
# upwork-contracts.sh — Show Upwork contract pipeline status.
# Usage: upwork-contracts.sh [--active|--all]
set -euo pipefail

DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
DB_PATH="${RICK_DB_PATH:-$DATA_ROOT/rick.db}"
UPWORK_DIR="$DATA_ROOT/upwork"

FILTER="active"
[[ "${1:-}" == "--all" ]] && FILTER="all"

if [[ ! -f "$DB_PATH" ]]; then
    echo "Database not found at $DB_PATH"
    exit 1
fi

echo "=== Upwork Contract Pipeline ==="
echo ""

if [[ "$FILTER" == "all" ]]; then
    sqlite3 -header -column "$DB_PATH" \
        "SELECT id, title, status, stage, priority, created_at
         FROM workflows
         WHERE kind = 'upwork_contract'
         ORDER BY priority ASC, created_at DESC
         LIMIT 20"
else
    sqlite3 -header -column "$DB_PATH" \
        "SELECT id, title, status, stage, priority, created_at
         FROM workflows
         WHERE kind = 'upwork_contract'
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
     WHERE w.kind = 'upwork_contract'
       AND j.status IN ('queued', 'running', 'blocked')
     ORDER BY j.step_index ASC
     LIMIT 20"

echo ""
echo "=== Contract Directories ==="
if [[ -d "$UPWORK_DIR/contracts" ]]; then
    ls -1 "$UPWORK_DIR/contracts" 2>/dev/null || echo "(none)"
else
    echo "(none)"
fi
