#!/usr/bin/env python3
"""
self-improvement-loop.py — Daily error→rule detector for Rick

Scans ERRORS.md and LEARNINGS.md for repeated patterns (2+ occurrences),
generates candidate MEMORY.md rules, stages them for review, and alerts
Vlad via the Approvals Telegram topic.

Usage:
  python3 self-improvement-loop.py              # full run
  python3 self-improvement-loop.py --dry-run    # detect only, no writes
  python3 self-improvement-loop.py --scan-only  # just print clusters

Cron spec: daily, 11:30 PM PT, model: haiku, timeout: 120s
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
WORKSPACE = Path(os.getenv("RICK_OPENCLAW_HOME", str(Path.home() / ".openclaw/workspace")))
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LEARNINGS_DIR = WORKSPACE / ".learnings"
ERRORS_FILE = LEARNINGS_DIR / "ERRORS.md"
LEARNINGS_FILE = LEARNINGS_DIR / "LEARNINGS.md"
STAGED_DIR = DATA_ROOT / "self-improvement/staged-rules"
HISTORY_FILE = DATA_ROOT / "self-improvement/rule-history.jsonl"
CLUSTER_CACHE = DATA_ROOT / "self-improvement/cluster-cache.json"
TG_SCRIPT = WORKSPACE / "scripts/tg-topic.sh"
MEMORY_FILE = WORKSPACE / "MEMORY.md"

NOW = datetime.now(timezone.utc).isoformat()
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ensure_dirs():
    STAGED_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)


# ── Parse error entries ───────────────────────────────────────────────────────
def parse_entries(filepath: Path) -> list:
    """Parse markdown entries from ERRORS.md or LEARNINGS.md."""
    if not filepath.exists():
        return []
    text = filepath.read_text()
    entries = []
    # Split on ## [ERR-... or ## [LRN-... headers
    blocks = re.split(r'\n(?=## \[(?:ERR|LRN)-)', text)
    for block in blocks:
        block = block.strip()
        if not block.startswith("## ["):
            continue
        # Extract ID
        id_match = re.match(r'## \[((?:ERR|LRN)-[^\]]+)\]', block)
        if not id_match:
            continue
        entry_id = id_match.group(1)
        # Extract fields
        summary_match = re.search(r'### Summary\s*\n(.+?)(?:\n###|\n---|\Z)', block, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else ""
        error_match = re.search(r'### Error\s*\n```[^\n]*\n(.+?)```', block, re.DOTALL)
        error_text = error_match.group(1).strip() if error_match else ""
        fix_match = re.search(r'### Suggested Fix\s*\n(.+?)(?:\n###|\n---|\Z)', block, re.DOTALL)
        suggested_fix = fix_match.group(1).strip() if fix_match else ""
        status_match = re.search(r'\*\*Status\*\*:\s*(\w+)', block)
        status = status_match.group(1) if status_match else "pending"
        logged_match = re.search(r'\*\*Logged\*\*:\s*(.+)', block)
        logged = logged_match.group(1).strip() if logged_match else ""
        # Extract a tag for the entry type (first word after the ID bracket)
        tag_match = re.match(r'## \[[^\]]+\]\s+(\S+)', block)
        tag = tag_match.group(1) if tag_match else ""
        entries.append({
            "id": entry_id,
            "tag": tag,
            "summary": summary,
            "error_text": error_text[:300],
            "suggested_fix": suggested_fix,
            "status": status,
            "logged": logged,
            "raw_len": len(block),
        })
    return entries


# ── Cluster similar errors ────────────────────────────────────────────────────
def normalize_for_clustering(text: str) -> str:
    """Normalize text for fuzzy matching."""
    text = text.lower()
    # Remove timestamps, IDs, paths
    text = re.sub(r'\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}[:\d.Z+\-]*', '', text)
    text = re.sub(r'/[\w/.~-]+', '<path>', text)
    text = re.sub(r'\b\d+\b', '<N>', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def fingerprint_error(entry: dict) -> str:
    """Create a fingerprint for grouping similar errors."""
    # Combine tag + normalized summary + key error tokens
    parts = [
        entry["tag"].lower(),
        normalize_for_clustering(entry["summary"][:100]),
    ]
    if entry["error_text"]:
        # Extract the core error type/message
        first_line = entry["error_text"].split('\n')[0][:80]
        parts.append(normalize_for_clustering(first_line))
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def cluster_entries(entries: list) -> dict:
    """Group entries by fingerprint. Returns {fingerprint: [entries]}."""
    clusters = defaultdict(list)
    for e in entries:
        fp = fingerprint_error(e)
        clusters[fp].append(e)
    return dict(clusters)


# ── Generate rules from clusters ──────────────────────────────────────────────
def generate_rule(cluster: list) -> Optional[dict]:
    """Generate a candidate MEMORY.md rule from a cluster of 2+ similar errors."""
    if len(cluster) < 2:
        return None

    # Use the most detailed suggested fix, or combine them
    fixes = [e["suggested_fix"] for e in cluster if e["suggested_fix"]]
    summaries = [e["summary"] for e in cluster]
    tags = list(set(e["tag"] for e in cluster if e["tag"]))
    ids = [e["id"] for e in cluster]

    # Pick the best fix (longest, most specific)
    best_fix = max(fixes, key=len) if fixes else "Review and add permanent rule."

    # Condense into a rule
    rule_id = f"rule-{TODAY}-{hashlib.sha1('|'.join(ids).encode()).hexdigest()[:8]}"

    rule = {
        "id": rule_id,
        "created_at": NOW,
        "occurrences": len(cluster),
        "source_ids": ids,
        "tags": tags,
        "pattern": summaries[0][:200],
        "all_summaries": summaries,
        "proposed_rule": distill_rule(summaries, best_fix),
        "suggested_fix": best_fix,
        "status": "staged",
        "approved": False,
    }
    return rule


def distill_rule(summaries: list, fix: str) -> str:
    """Distill error cluster into a concise MEMORY.md rule."""
    # Identify common keywords across summaries
    combined = " ".join(summaries).lower()
    
    # Simple heuristic distillation — no LLM needed for most cases
    if "himalaya" in combined and ("query" in combined or "output" in combined or "-o" in combined):
        return "Himalaya: always put flags (-o, --output) BEFORE the search query string."
    if "stripe" in combined and "expand" in combined and "depth" in combined:
        return "Stripe API: keep expand[] depth <= 4 levels. Fetch nested objects separately."
    if "curl" in combined and "bracket" in combined:
        return "Curl + Stripe: use -G --data-urlencode for params with brackets, or -g/--globoff."
    if "playwright" in combined and ("import" in combined or "module" in combined):
        return "Playwright Node: import from /opt/homebrew/lib/node_modules/playwright/index.mjs, not bare 'playwright'."
    if "shell" in combined and ("quoting" in combined or "apostrophe" in combined or "zsh" in combined):
        return "Shell scripting: use here-docs for multiline text with special chars. Never embed apostrophes in single-quoted vars."

    # Fallback: use the fix as the rule, shortened
    if len(fix) <= 200:
        return fix
    return fix[:200].rsplit(' ', 1)[0] + "..."


# ── LLM-assisted rule generation for complex clusters ────────────────────────
def llm_distill_rule(cluster: list) -> Optional[str]:
    """Use LLM to distill complex error clusters into a MEMORY.md rule."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None

    summaries = "\n".join(f"- {e['summary']}" for e in cluster[:5])
    fixes = "\n".join(f"- {e['suggested_fix']}" for e in cluster[:5] if e['suggested_fix'])

    prompt = f"""You are generating a permanent operating rule for an AI agent's MEMORY.md.

These errors have occurred {len(cluster)} times:
{summaries}

Suggested fixes:
{fixes}

Write ONE concise rule (1-2 sentences max) that prevents this class of error permanently.
Format: imperative, specific, actionable. Example: "Himalaya: always put flags before search query string."
Return ONLY the rule text."""

    try:
        import urllib.request
        payload = json.dumps({
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 150,
        })
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload.encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip().strip('"')
    except Exception:
        return None


# ── Stage rules for review ────────────────────────────────────────────────────
def stage_rule(rule: dict) -> Path:
    """Write a staged rule to disk for review."""
    filepath = STAGED_DIR / f"{rule['id']}.json"
    filepath.write_text(json.dumps(rule, indent=2))
    return filepath


def log_to_history(rule: dict, action: str):
    """Append to rule history JSONL."""
    entry = {
        "timestamp": NOW,
        "rule_id": rule["id"],
        "action": action,
        "occurrences": rule["occurrences"],
        "pattern": rule["pattern"][:200],
        "proposed_rule": rule["proposed_rule"],
    }
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Alert Vlad ────────────────────────────────────────────────────────────────
def alert_approvals(rules: list):
    """Send staged rules summary to Telegram Approvals topic."""
    if not rules:
        return
    lines = ["🧠 **Self-Improvement: New Rule Candidates**\n"]
    for r in rules:
        lines.append(f"• [{r['occurrences']}x] {r['proposed_rule']}")
        lines.append(f"  Sources: {', '.join(r['source_ids'][:3])}")
    lines.append(f"\n📁 Staged at: self-improvement/staged-rules/")
    lines.append("Reply 'approve <rule-id>' or review in next nightly.")
    msg = "\n".join(lines)
    # Primary: openclaw message send → approvals (chat -1003781085932, tid 26)
    try:
        r = subprocess.run(
            [
                "openclaw", "message", "send",
                "--channel", "telegram",
                "--target", "-1003781085932",
                "--thread-id", "26",
                "--message", msg,
            ],
            capture_output=True, timeout=15,
        )
        if r.returncode == 0:
            return
    except Exception:
        pass
    # Fallback: tg-topic.sh
    try:
        subprocess.run(
            ["bash", str(TG_SCRIPT), "approvals", msg],
            capture_output=True, timeout=10
        )
    except Exception as e:
        print(f"[self-improvement] TG alert failed: {e}")


# ── Check for already-staged duplicates ───────────────────────────────────────
def load_existing_staged() -> set:
    """Return set of fingerprints already staged."""
    fps = set()
    for f in STAGED_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            for sid in data.get("source_ids", []):
                fps.add(sid)
        except Exception:
            pass
    return fps


# ── Main pipeline ─────────────────────────────────────────────────────────────
def run(dry_run: bool = False, scan_only: bool = False):
    ensure_dirs()
    
    # 1. Parse all entries
    errors = parse_entries(ERRORS_FILE)
    learnings = parse_entries(LEARNINGS_FILE)
    all_entries = errors + learnings
    
    # Filter to pending/in_progress only
    active = [e for e in all_entries if e["status"] in ("pending", "in_progress")]
    print(f"[self-improvement] Parsed {len(errors)} errors, {len(learnings)} learnings ({len(active)} active)")
    
    # 2. Cluster similar entries
    clusters = cluster_entries(active)
    repeated = {k: v for k, v in clusters.items() if len(v) >= 2}
    print(f"[self-improvement] Found {len(clusters)} clusters, {len(repeated)} repeated (2+)")
    
    if scan_only:
        for fp, entries in sorted(repeated.items(), key=lambda x: -len(x[1])):
            print(f"\n  Cluster {fp} ({len(entries)}x):")
            for e in entries:
                print(f"    - {e['id']}: {e['summary'][:80]}")
        return
    
    # 3. Check what's already staged
    already_staged = load_existing_staged()
    
    # 4. Generate rules for new clusters
    new_rules = []
    for fp, cluster in repeated.items():
        # Skip if all source IDs already staged
        source_ids = {e["id"] for e in cluster}
        if source_ids.issubset(already_staged):
            continue
        
        rule = generate_rule(cluster)
        if not rule:
            continue
        
        # Try LLM distillation for large clusters
        if len(cluster) >= 3:
            llm_rule = llm_distill_rule(cluster)
            if llm_rule:
                rule["proposed_rule"] = llm_rule
        
        new_rules.append(rule)
        print(f"[self-improvement] Generated rule: {rule['id']}")
        print(f"  Pattern: {rule['pattern'][:100]}")
        print(f"  Proposed: {rule['proposed_rule']}")
    
    if not new_rules:
        print("[self-improvement] No new rules to stage.")
        return
    
    if dry_run:
        print(f"\n[self-improvement] DRY RUN — would stage {len(new_rules)} rules:")
        for r in new_rules:
            print(f"  - {r['id']}: {r['proposed_rule']}")
        return
    
    # 5. Stage rules and alert
    for rule in new_rules:
        stage_rule(rule)
        log_to_history(rule, "staged")
        print(f"[self-improvement] Staged: {rule['id']}")
    
    alert_approvals(new_rules)
    print(f"[self-improvement] Done. Staged {len(new_rules)} new rules.")


# ── Approve a staged rule (called by cron or manual) ─────────────────────────
def approve_rule(rule_id: str):
    """Move a staged rule into MEMORY.md."""
    rule_file = STAGED_DIR / f"{rule_id}.json"
    if not rule_file.exists():
        print(f"[self-improvement] Rule not found: {rule_id}")
        sys.exit(1)
    
    rule = json.loads(rule_file.read_text())
    
    # Append to MEMORY.md under Operating Patterns
    memory = MEMORY_FILE.read_text()
    insertion_point = "## Operating Patterns"
    if insertion_point not in memory:
        insertion_point = "## ⛔ PERMANENT RULES"
    
    new_entry = f"\n- {rule['proposed_rule']}  <!-- auto-promoted {TODAY} from {', '.join(rule['source_ids'][:3])} -->"
    
    # Insert after the section header
    idx = memory.index(insertion_point)
    next_newline = memory.index("\n", idx)
    memory = memory[:next_newline] + new_entry + memory[next_newline:]
    
    MEMORY_FILE.write_text(memory)
    
    # Update rule status
    rule["status"] = "approved"
    rule["approved"] = True
    rule["approved_at"] = NOW
    rule_file.write_text(json.dumps(rule, indent=2))
    
    log_to_history(rule, "approved")
    print(f"[self-improvement] Approved and added to MEMORY.md: {rule['proposed_rule']}")


def main():
    parser = argparse.ArgumentParser(description="Rick self-improvement loop: error→rule detector")
    parser.add_argument("--dry-run", action="store_true", help="Detect patterns only, no writes")
    parser.add_argument("--scan-only", action="store_true", help="Print clusters and exit")
    parser.add_argument("--approve", type=str, help="Approve a staged rule by ID")
    args = parser.parse_args()
    
    if args.approve:
        approve_rule(args.approve)
    else:
        run(dry_run=args.dry_run, scan_only=args.scan_only)


if __name__ == "__main__":
    main()
