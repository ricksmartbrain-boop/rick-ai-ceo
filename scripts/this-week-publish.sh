#!/bin/sh
# ai.rick.this-week-publish — Mondays 09:00: regenerate /this-week and push.
# Pull first and retry-once on rejected push: the receipts auto-publish job
# also pushes to this repo.
# 2026-07-13: trivial-delta gate — this-week-page.py exits 3 when the period
# had 0 commits / 0 posts / 0 revenue change; we log SKIPPED_TRIVIAL and stop.
set -e
SITE="$HOME/.openclaw/workspace/meetrick-site"
cd "$SITE"
git pull --rebase --autostash
rc=0
/usr/bin/python3 "$HOME/.openclaw/workspace/scripts/this-week-page.py" --out "$SITE/this-week.html" || rc=$?
if [ "$rc" -eq 3 ]; then
  echo "SKIPPED_TRIVIAL this-week: no commits/posts/revenue change in period — not publishing"
  exit 0
fi
if [ "$rc" -ne 0 ]; then
  exit "$rc"
fi
if ! git diff --quiet -- this-week.html; then
  git add this-week.html
  git commit -m "this-week: auto-publish $(date +%Y-%m-%d)"
  git push origin main || { git pull --rebase --autostash && git push origin main; }
fi
