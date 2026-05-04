#!/usr/bin/env python3
"""Newsletter memory + overlap detection.

Public interface
----------------
    backfill_ledger(issues_dir: Path, ledger_path: Path) -> int
    append_issue(ledger_path: Path, issue: dict) -> None
    last_n_issues(ledger_path: Path, n: int = 6) -> list[dict]
    detect_overlap(draft: dict, history: list[dict]) -> list[dict]

Spec (Vlad TUI handoff, 2026-05-04):
- Ledger lives at ~/rick-vault/operations/newsletter-ledger.jsonl (JSONL, append-only).
- Hard-fail on subject / hook / CTA / key-number overlap with last 6 issues.
- Reuse execution-ledger.py append pattern (atomic flush, one event per line).
- Smart-models invariant: route stays writing -> claude-sonnet-4-6. Never
  gpt-5.4-mini for personalization (this module is plain-text comparison,
  no LLM call needed for overlap detection itself).

CLI
---
    python3 -m runtime.newsletter_memory backfill
    python3 -m runtime.newsletter_memory check <draft.json>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LEDGER_PATH = DATA_ROOT / "operations" / "newsletter-ledger.jsonl"
ISSUES_DIR = Path(__file__).resolve().parents[1] / "skills" / "newsletter" / "issues"

# Theme rotation for fallback/auto-assign — slot 1..6 maps to issues 1..6, 7..12, etc.
THEMES = [
    "proof-receipts",       # 1: revenue numbers, screenshots, receipts
    "lesson-failure",       # 2: post-mortem, what broke, why
    "tactical-playbook",    # 3: how-to, repeatable system
    "behind-the-scenes",    # 4: ops detail, model routing, infra
    "contrarian-take",      # 5: opinion piece against consensus
    "tools-stack-reveal",   # 6: tooling, integrations, what powers Rick
]


def slot_for_issue(issue_num: int) -> str:
    """1->slot1, 2->slot2, ..., 7->slot1 again."""
    return THEMES[(issue_num - 1) % len(THEMES)]


# ---------------------------------------------------------------------------
# Atomic append (mirrors execution-ledger.py pattern)
# ---------------------------------------------------------------------------

def _atomic_append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ---------------------------------------------------------------------------
# Hook + key-number extraction
# ---------------------------------------------------------------------------

# Heuristic: a "hook" is the first sentence/clause of the subject, lowered.
_NUM_PAT = re.compile(r"\$?\d[\d,]*(?:\.\d+)?[%KMkm]?")

# Numbers that legitimately appear in nearly every issue and shouldn't trip
# the overlap detector. The standing MRR ($9) is the obvious one. Common
# small integers (1, 2, 3, days, weeks) are also too generic to flag.
_KEY_NUMBER_IGNORE = {"$9", "$0", "0", "1", "2", "3", "7", "24", "100"}


def extract_hook(subject: str) -> str:
    """Return a normalized first-clause fingerprint of the subject."""
    if not subject:
        return ""
    s = subject.strip().lower()
    # Cut at first em-dash, colon, period, or question mark.
    for sep in ["—", " - ", ": ", ". ", "? ", "! "]:
        if sep in s:
            s = s.split(sep, 1)[0]
            break
    # Drop weak filler words that don't differentiate hooks.
    s = re.sub(r"\b(the|a|an|just|my|our|i|we)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_key_numbers(text: str) -> set[str]:
    """Return distinct *signature* numeric tokens.

    Filters out trivial/recurring numbers (single digits, the standing MRR
    $9, common time units) so the overlap detector only fails on real
    repeat-receipts like '$538' or '752 leads'. Tightened 2026-05-04 after
    the first run blocked a clean issue #5 draft over '1' and '$9'.
    """
    if not text:
        return set()
    raw = {m.group(0).rstrip(".,") for m in _NUM_PAT.finditer(text)}
    out: set[str] = set()
    for tok in raw:
        if tok in _KEY_NUMBER_IGNORE:
            continue
        # Drop bare 1- and 2-digit integers without currency/% suffix.
        bare = tok.lstrip("$").rstrip("%KMkm")
        if not bare:
            continue
        if tok == bare and bare.isdigit() and len(bare) <= 2:
            continue
        out.add(tok)
    return out


def _topics_text(topics: Iterable[str]) -> str:
    return " | ".join(t for t in topics if t)


# ---------------------------------------------------------------------------
# Ledger build / read
# ---------------------------------------------------------------------------

def _issue_to_ledger_row(raw: dict) -> dict:
    """Normalize a raw issue dict to a ledger row."""
    issue_num = int(raw.get("issue") or 0)
    subject = raw.get("subject", "") or ""
    topics = raw.get("topics") or []
    cta = raw.get("cta", "") or ""
    date = raw.get("date") or raw.get("sent_at", "")[:10] if raw.get("sent_at") else ""
    body_blob = " ".join([subject, _topics_text(topics), cta])
    return {
        "issue": issue_num,
        "date": date,
        "subject": subject,
        "hook": extract_hook(subject),
        "topics": list(topics),
        "cta": cta,
        "key_numbers": sorted(extract_key_numbers(body_blob)),
        "theme": raw.get("theme") or slot_for_issue(issue_num) if issue_num else "",
        "sent_at": raw.get("sent_at", ""),
        "broadcast_id": raw.get("broadcast_id", ""),
        "audience_id": raw.get("audience_id", ""),
    }


def backfill_ledger(issues_dir: Path = ISSUES_DIR, ledger_path: Path = LEDGER_PATH) -> int:
    """Read every issue-NNN.json and rewrite the ledger from scratch.

    Returns count of rows written.
    """
    rows: list[dict] = []
    for f in sorted(issues_dir.glob("issue-*.json")):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  skip {f.name}: {exc}", file=sys.stderr)
            continue
        rows.append(_issue_to_ledger_row(raw))
    rows.sort(key=lambda r: r.get("issue") or 0)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(ledger_path)
    return len(rows)


def append_issue(ledger_path: Path, issue: dict) -> None:
    """Append a row after a successful send.

    `issue` may be either a raw issue dict (with `subject`, `topics`, etc.)
    or a pre-normalized ledger row.
    """
    if "hook" in issue and "key_numbers" in issue:
        row = issue
    else:
        row = _issue_to_ledger_row(issue)
    _atomic_append(ledger_path, json.dumps(row, ensure_ascii=False))


def last_n_issues(ledger_path: Path = LEDGER_PATH, n: int = 6) -> list[dict]:
    if not ledger_path.exists():
        return []
    rows: list[dict] = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    rows.sort(key=lambda r: r.get("issue") or 0)
    return rows[-n:]


# ---------------------------------------------------------------------------
# Overlap detection
# ---------------------------------------------------------------------------

def detect_overlap(draft: dict, history: list[dict]) -> list[dict]:
    """Return a list of overlap findings. Each finding:
        {"kind": "subject"|"hook"|"cta"|"key_number", "value": "...", "issue": N}

    Subject overlap: exact case-insensitive match.
    Hook overlap: normalized first-clause match.
    CTA overlap: exact case-insensitive match.
    Key-number overlap: ANY number in draft also appears in any of last 6 issues.
    """
    findings: list[dict] = []
    if not history:
        return findings

    d_subject = (draft.get("subject") or "").strip().lower()
    d_hook = extract_hook(draft.get("subject") or "")
    d_cta = (draft.get("cta") or "").strip().lower()

    body_blob = " ".join([
        draft.get("subject") or "",
        _topics_text(draft.get("topics") or []),
        draft.get("cta") or "",
        draft.get("body_md") or "",
    ])
    d_numbers = extract_key_numbers(body_blob)

    for prior in history:
        p_issue = prior.get("issue")
        p_subject = (prior.get("subject") or "").strip().lower()
        p_hook = (prior.get("hook") or "").strip().lower()
        p_cta = (prior.get("cta") or "").strip().lower()
        p_numbers = set(prior.get("key_numbers") or [])

        if d_subject and d_subject == p_subject:
            findings.append({"kind": "subject", "value": prior.get("subject"), "issue": p_issue})
        if d_hook and d_hook == p_hook:
            findings.append({"kind": "hook", "value": prior.get("hook"), "issue": p_issue})
        if d_cta and d_cta == p_cta:
            findings.append({"kind": "cta", "value": prior.get("cta"), "issue": p_issue})
        for n in d_numbers & p_numbers:
            findings.append({"kind": "key_number", "value": n, "issue": p_issue})
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_backfill(args: argparse.Namespace) -> int:
    n = backfill_ledger()
    print(f"backfilled {n} issues -> {LEDGER_PATH}")
    if n:
        for row in last_n_issues(n=n):
            print(f"  #{row['issue']} {row['date']} | theme={row['theme']} | {row['subject'][:60]}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    draft_path = Path(args.draft)
    if not draft_path.exists():
        print(f"draft not found: {draft_path}", file=sys.stderr)
        return 2
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    history = last_n_issues(n=6)
    findings = detect_overlap(draft, history)
    if findings:
        print(f"OVERLAP DETECTED ({len(findings)} findings):")
        for f in findings:
            print(f"  [{f['kind']}] '{f['value']}' (issue #{f['issue']})")
        return 2
    print("OK — no overlap with last 6 issues.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backfill")
    pc = sub.add_parser("check")
    pc.add_argument("draft")
    args = p.parse_args()
    if args.cmd == "backfill":
        return _cmd_backfill(args)
    if args.cmd == "check":
        return _cmd_check(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
