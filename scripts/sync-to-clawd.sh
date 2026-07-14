#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLAWD_DIR="$HOME/clawd"

if [[ ! -d "$CLAWD_DIR" ]]; then
  echo "[sync] ~/clawd not found — skipping sync"
  exit 0
fi

echo "[sync] syncing $REPO_DIR → $CLAWD_DIR"
rsync -a --delete \
  --exclude='config/' \
  --exclude='.venv/' \
  --exclude='node_modules/' \
  --exclude='.git/' \
  --exclude='__pycache__/' \
  "$REPO_DIR/skills/" "$CLAWD_DIR/skills/"
rsync -a --delete \
  --exclude='__pycache__/' \
  "$REPO_DIR/scripts/" "$CLAWD_DIR/scripts/"
rsync -a --delete \
  --exclude='__pycache__/' \
  "$REPO_DIR/runtime/" "$CLAWD_DIR/runtime/"
echo "[sync] done"
