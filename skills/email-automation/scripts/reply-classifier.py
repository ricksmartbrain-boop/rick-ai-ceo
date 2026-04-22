#!/usr/bin/env python3
"""Phase G classifier — reads inbound email triage JSONL + classifies into 4 buckets.

Input: ~/rick-vault/mailbox/triage/inbound-YYYY-MM-DD.jsonl
       (populated by imap_watcher.py OR manual drop OR future Resend inbound webhook)
Each line: {"id": "...", "from": "...", "subject": "...", "body": "...", "received_at": "..."}

Output: same file, rewrites with added `classification` + `classified_at` fields.

Labels: sales_inquiry | objection | not_interested | unsubscribe

Runs every 10min via ai.rick.reply-router.plist alongside reply_router.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2].parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.llm import generate_text  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
TRIAGE_DIR = DATA_ROOT / "mailbox" / "triage"
LOG_FILE = DATA_ROOT / "operations" / "reply-classifier.jsonl"

LABELS = {"sales_inquiry", "objection", "not_interested", "unsubscribe"}

CLASSIFIER_PROMPT = """You are a reply-classifier. Read this inbound email and return EXACTLY ONE of these four labels (no other text):

- sales_inquiry: person is curious, asking questions, wants to learn more, wants a demo, wants pricing
- objection: person pushes back, raises concerns, has objections we can rebut
- not_interested: polite decline, "not right now", "thanks but no", "maybe later"
- unsubscribe: explicit request to stop emailing, "unsubscribe", "remove me", "stop"

Email:
FROM: {from_addr}
SUBJECT: {subject}
BODY:
{body}

Return only the single label, nothing else."""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log_event(event: dict):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ran_at": now_iso(), **event}) + "\n")
    except OSError:
        pass


def classify_one(row: dict) -> str:
    """Returns one of the 4 labels. Falls back to 'not_interested' on any error."""
    body = (row.get("body") or "")[:2000]
    subject = (row.get("subject") or "")[:200]
    from_addr = row.get("from") or ""
    # Cheap regex pre-check for unsubscribe (no LLM cost when obvious)
    body_low = body.lower()
    if any(k in body_low for k in ("unsubscribe", "remove me from", "stop emailing", "opt out", "take me off")):
        return "unsubscribe"
    prompt = CLASSIFIER_PROMPT.format(from_addr=from_addr, subject=subject, body=body)
    try:
        result = generate_text("writing", prompt, fallback="not_interested")
        text = (result.content if hasattr(result, "content") else str(result)).strip().lower()
    except Exception:
        return "not_interested"
    # Validate — strict allow-list
    for label in LABELS:
        if label in text:
            return label
    return "not_interested"


def process_file(path: Path, dry_run: bool, batch_cap: int) -> dict:
    if not path.exists():
        return {"file": str(path), "classified": 0, "skipped": 0, "errors": 0}
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return {"file": str(path), "classified": 0, "skipped": 0, "errors": 1}
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    classified = 0
    skipped = 0
    errors = 0
    for row in rows:
        if row.get("classification"):
            skipped += 1
            continue
        if classified >= batch_cap:
            break
        try:
            label = classify_one(row)
            row["classification"] = label
            row["classified_at"] = now_iso()
            classified += 1
            if dry_run:
                print(f"[dry] {row.get('from','?')}: {label}")
        except Exception as exc:
            row["classification_error"] = str(exc)[:200]
            errors += 1
    if classified and not dry_run:
        try:
            path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")
        except OSError as exc:
            errors += 1
            log_event({"action": "write-failed", "file": str(path), "error": str(exc)})
    return {"file": str(path), "classified": classified, "skipped": skipped, "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    ap.add_argument("--batch", type=int, default=10)
    args = ap.parse_args()

    TRIAGE_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(TRIAGE_DIR.glob("inbound-*.jsonl"))
    if not files:
        summary = {"dry_run": args.dry_run, "files": 0, "classified": 0, "message": "no triage files yet"}
        log_event(summary)
        print(json.dumps(summary, indent=2))
        return 0

    totals = {"classified": 0, "skipped": 0, "errors": 0, "files": 0}
    for f in files:
        result = process_file(f, args.dry_run, args.batch)
        totals["classified"] += result["classified"]
        totals["skipped"] += result["skipped"]
        totals["errors"] += result["errors"]
        totals["files"] += 1
        if totals["classified"] >= args.batch:
            break
    summary = {"dry_run": args.dry_run, **totals, "ran_at": now_iso()}
    log_event(summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
