#!/usr/bin/env bash
# Generates ~/rick-vault/memory/hot-context.md
# Injected into every session via HOT-CONTEXT.md symlink. Target: <3KB.

set -euo pipefail
VAULT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
OUTPUT="$VAULT/memory/hot-context.md"
STATE="$VAULT/brain/state.json"
DAILY="$VAULT/memory/$(date +%Y-%m-%d).md"
YESTERDAY="$VAULT/memory/$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d 'yesterday' +%Y-%m-%d).md"

{
echo "# Hot Context"
echo "> Auto-generated $(date -u '+%Y-%m-%dT%H:%M:%SZ'). Do not edit manually."
echo ""

echo "## Revenue Snapshot"
# Parse real MRR from latest revenue/reconciliation-*.md (per SELF-FAQ
# rule: NEVER surface phantom $547 from state.json; real MRR = $9).
python3 -c "
import re
from pathlib import Path
revdir = Path('$VAULT') / 'revenue'
real_mrr = 9.0
src = 'fallback (no reconciliation file)'
if revdir.is_dir():
    recs = sorted(revdir.glob('reconciliation-*.md'))
    if recs:
        text = recs[-1].read_text(encoding='utf-8', errors='replace')
        m = re.search(r'Real current MRR[:\*\s]+\\\$?([0-9]+(?:\.[0-9]+)?)', text)
        if m:
            real_mrr = float(m.group(1))
            src = recs[-1].name
print(f'- MRR: \${real_mrr:.2f}/mo (real, phantom \$547 stripped — source: {src})')
print('- Customers: 1 paying real subscription (sub_1TEGyAD9G3v6e0Osa0sgsrVk)')
" 2>/dev/null || echo "- MRR: \$9/mo (fallback)"
echo ""

echo "## Top 3 Active Projects"
python3 -c "
import os, json
from pathlib import Path
vault = Path('$VAULT')
projects_dir = vault / 'projects'
if not projects_dir.exists():
    print('- No projects dir found')
else:
    dirs = sorted(projects_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    count = 0
    for d in dirs:
        if count >= 3: break
        summary = d / 'summary.md'
        if summary.exists():
            lines = summary.read_text().strip().split('\n')
            first = next((l for l in lines if l.strip() and not l.startswith('#')), '')
            print(f'- **{d.name}**: {first[:80]}')
            count += 1
" 2>/dev/null || echo "- Check vault/projects/"
echo ""

echo "## Today's Priorities"
python3 -c "
import json
from pathlib import Path
state_file = Path('$STATE')
if state_file.exists():
    s = json.loads(state_file.read_text())
    prios = s.get('priorities', [])
    if prios:
        for i, p in enumerate(prios[:3], 1):
            text = p if isinstance(p, str) else p.get('title', str(p))
            print(f'{i}. {text}')
    else:
        print('- None set in state.json')
else:
    print('- state.json missing')
" 2>/dev/null || echo "- Check state.json"
echo ""

echo "## Open Blockers"
python3 -c "
import json
from pathlib import Path
state_file = Path('$STATE')
if state_file.exists():
    s = json.loads(state_file.read_text())
    blockers = s.get('blockers', [])
    if blockers:
        for b in blockers[:3]:
            text = b if isinstance(b, str) else b.get('text', str(b))
            print(f'- {text}')
    else:
        print('- None')
else:
    print('- None')
" 2>/dev/null || echo "- None"
echo ""

echo "## Recent Wins (last 48h)"
# Filter the phantom $547 MRR / 538-velocity that legacy daily-note emitters keep
# surfacing. Real MRR is on the dedicated MRR line above. Tier-2 follow-up:
# patch the upstream emitter (velocity/Stripe-poll cron writing $547 into notes).
if [ -f "$DAILY" ]; then
    grep -i "shipped\|won\|launched\|paid\|revenue\|completed\|✅" "$DAILY" 2>/dev/null \
        | grep -ivE 'MRR[ =:]*\$?547|velocity.*538' \
        | head -3 | sed 's/^/- /' || echo "- None logged today"
elif [ -f "$YESTERDAY" ]; then
    grep -i "shipped\|won\|launched\|paid\|revenue\|completed\|✅" "$YESTERDAY" 2>/dev/null \
        | grep -ivE 'MRR[ =:]*\$?547|velocity.*538' \
        | head -3 | sed 's/^/- /' || echo "- None logged yesterday"
else
    echo "- No daily notes found"
fi
echo ""

echo "## Active Coding Sessions"
tmux -S ~/.tmux/sock list-sessions 2>/dev/null | head -5 | sed 's/^/- /' || echo "- None"
echo ""

echo "## Next 24h"
python3 -c "
import json
from pathlib import Path
state_file = Path('$STATE')
if state_file.exists():
    s = json.loads(state_file.read_text())
    deadlines = s.get('deadlines', [])
    if deadlines:
        for d in deadlines[:3]:
            text = d if isinstance(d, str) else d.get('text', str(d))
            print(f'- {text}')
    else:
        print('- Nothing scheduled')
else:
    print('- Nothing scheduled')
" 2>/dev/null || echo "- Nothing scheduled"

} | head -c 3072 > "$OUTPUT"

echo "Generated $OUTPUT ($(wc -c < "$OUTPUT" | tr -d ' ') bytes)"
