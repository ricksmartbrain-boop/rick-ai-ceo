#!/usr/bin/env python3
"""Daily memory maintenance for Rick.
1. Rebuilds memory index
2. Generates hot-context.md
3. Purges polluted auto-promoted entries (FETCH ERROR / dashboard dumps / >600 chars)
4. Archives auto-promoted patterns older than 7 days to COLD
5. Enforces MEMORY.md < 10KB (alerts if over)
"""

import subprocess, sys, os, json, re
from pathlib import Path
from datetime import datetime, timedelta

WORKSPACE = Path.home() / ".openclaw" / "workspace"
VAULT = Path(os.getenv("RICK_DATA_ROOT", Path.home() / "rick-vault"))
MEMORY_HOT = WORKSPACE / "MEMORY.md"
MEMORY_COLD = VAULT / "memory" / "MEMORY-COLD.md"
INDEX_SCRIPT = WORKSPACE / "skills" / "obsidian-memory" / "scripts" / "rebuild-memory-index.py"
HOT_CTX_SCRIPT = WORKSPACE / "scripts" / "generate-hot-context.sh"

def run(cmd):
    """Run a command. Accepts a string (split via shlex) or list."""
    import shlex
    if isinstance(cmd, str):
        cmd_list = shlex.split(cmd)
    else:
        cmd_list = cmd
    print(f"  → {' '.join(cmd_list)}")
    r = subprocess.run(cmd_list, capture_output=True, text=True)
    if r.stdout.strip(): print(f"  {r.stdout.strip()[:200]}")
    if r.returncode != 0 and r.stderr: print(f"  WARN: {r.stderr.strip()[:200]}")
    return r

def rebuild_index():
    print("[1/4] Rebuilding memory index...")
    if INDEX_SCRIPT.exists():
        run(f"python3 {INDEX_SCRIPT} rebuild --write")
    else:
        print(f"  SKIP: {INDEX_SCRIPT} not found")

def generate_hot_context():
    print("[2/4] Generating hot-context.md...")
    if HOT_CTX_SCRIPT.exists():
        run(f"bash {HOT_CTX_SCRIPT}")
    else:
        print(f"  SKIP: {HOT_CTX_SCRIPT} not found")

def check_memory_size():
    print("[5/5] Checking MEMORY.md size...")
    size = MEMORY_HOT.stat().st_size if MEMORY_HOT.exists() else 0
    print(f"  MEMORY.md = {size:,} bytes ({size/1024:.1f}KB)")
    if size > 10240:
        print(f"  ⚠️  OVER 10KB — prune stale sections to COLD")
        return True
    print(f"  ✅ Under 10KB limit")
    return False

# Content filter for auto-promoted patterns (added 2026-07-12).
# A promoted memory should be a distilled lesson, not a report. The promoter
# itself lives in run-heartbeat.sh and blindly appends candidates from
# $RICK_DATA_ROOT/learning/patterns/ — this purge is the deterministic
# safety net that keeps MEMORY.md clean if junk ever lands there again.
EMOJI_HEADER_RE = re.compile(r'#+\s*[\U0001F000-\U0001FAFF☀-➿⬀-⯿]')

def is_polluted_pattern(entry: str) -> bool:
    """True if a promoted pattern bullet is a report dump, not a lesson."""
    if 'FETCH ERROR' in entry:
        return True
    if EMOJI_HEADER_RE.search(entry):
        return True
    if len(entry) > 600:
        return True
    return False

def purge_polluted_patterns():
    print("[3/5] Purging polluted auto-promoted entries...")
    if not MEMORY_HOT.exists():
        return
    lines = MEMORY_HOT.read_text().split('\n')
    kept, purged, i = [], 0, 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('- [pattern:') and is_polluted_pattern(line):
            purged += 1
            i += 1
            continue
        kept.append(line)
        i += 1
    # Drop "## Auto-Promoted Patterns (...)" headers left with no bullets.
    out = []
    for idx, line in enumerate(kept):
        if line.startswith('## Auto-Promoted Patterns ('):
            has_bullet = False
            for nxt in kept[idx + 1:]:
                if nxt.startswith('## '):
                    break
                if nxt.strip().startswith('- [pattern:'):
                    has_bullet = True
                    break
            if not has_bullet:
                # Also swallow one leading blank line before the header.
                while out and out[-1] == '':
                    out.pop()
                continue
        out.append(line)
    if purged:
        text = '\n'.join(out).rstrip('\n') + '\n'
        MEMORY_HOT.write_text(text)
        print(f"  Purged {purged} polluted entries")
    else:
        print("  No polluted entries found")

def archive_old_patterns():
    print("[4/5] Archiving old auto-promoted patterns...")
    if not MEMORY_HOT.exists():
        return
    content = MEMORY_HOT.read_text()
    cutoff = datetime.now() - timedelta(days=7)
    lines = content.split('\n')
    hot_lines, cold_additions, in_old, current = [], [], False, []

    for line in lines:
        if line.startswith('## Auto-Promoted Patterns ('):
            try:
                date_str = line.split('(')[1].split(')')[0]
                if datetime.strptime(date_str, '%Y-%m-%d') < cutoff:
                    in_old, current = True, [line]
                    continue
                else:
                    in_old = False
            except (IndexError, ValueError):
                in_old = False
        if in_old:
            if line.startswith('## ') and 'Auto-Promoted' not in line:
                cold_additions.extend(current + [''])
                in_old = False
                hot_lines.append(line)
            else:
                current.append(line)
            continue
        hot_lines.append(line)

    if cold_additions:
        cold_content = MEMORY_COLD.read_text() if MEMORY_COLD.exists() else "# MEMORY-COLD.md — Archive\n\n"
        MEMORY_COLD.write_text(cold_content + '\n' + '\n'.join(cold_additions))
        MEMORY_HOT.write_text('\n'.join(hot_lines))
        print(f"  Archived {len(cold_additions)} lines to COLD")
    else:
        print("  Nothing to archive (patterns < 7 days old)")

def main():
    print(f"=== Memory Maintenance {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    rebuild_index()
    generate_hot_context()
    purge_polluted_patterns()
    archive_old_patterns()
    over = check_memory_size()
    if over:
        print("\n⚠️  MEMORY.md still over 10KB after archive pass — needs manual prune")
    else:
        print("\n✅ Maintenance complete")

if __name__ == '__main__':
    main()
