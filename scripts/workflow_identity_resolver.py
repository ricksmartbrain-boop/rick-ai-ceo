"""
Workflow Identity Resolver v1
=============================
Single source of truth for workflow identity.

Usage:
    from scripts.workflow_identity_resolver import resolve_or_create_workflow, ResolveResult

    result = resolve_or_create_workflow(title="My Initiative", kind="initiative", rationale="...")
    if result.action == "needs_review":
        # DO NOT create — enqueued for review
        return
    workflow_uid = result.workflow_uid
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_VAULT = Path(os.getenv("RICK_VAULT", "/Users/rickthebot/rick-vault"))
_DATA = _VAULT / "data"
_REGISTRY_FILE = _DATA / "workflow-identity-registry.jsonl"
_REVIEW_QUEUE = _DATA / "workflow-identity-review-queue.jsonl"
_LOCK_FILE = _DATA / ".workflow-identity-registry.lock"

# Fuzzy match threshold: similarity >= this triggers needs_review instead of create
FUZZY_THRESHOLD = 0.70


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class IdentityRecord:
    workflow_uid: str
    objective_fingerprint: str
    canonical_title: str
    kind: str
    rationale: str
    status: str
    owner: str
    created_at: str
    last_seen_at: str
    aliases: list[str] = field(default_factory=list)
    source_paths: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    db_workflow_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "IdentityRecord":
        return cls(
            workflow_uid=d["workflow_uid"],
            objective_fingerprint=d["objective_fingerprint"],
            canonical_title=d["canonical_title"],
            kind=d["kind"],
            rationale=d.get("rationale", ""),
            status=d.get("status", "queued"),
            owner=d.get("owner", "rick"),
            created_at=d["created_at"],
            last_seen_at=d.get("last_seen_at", d["created_at"]),
            aliases=d.get("aliases", []),
            source_paths=d.get("source_paths", []),
            supersedes=d.get("supersedes", []),
            db_workflow_id=d.get("db_workflow_id", ""),
        )


@dataclass
class ResolveResult:
    action: str  # "reuse" | "create" | "needs_review"
    workflow_uid: Optional[str] = None
    record: Optional[IdentityRecord] = None
    candidates: list[IdentityRecord] = field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
# Normalization & fingerprinting
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compute_fingerprint(title: str, kind: str) -> str:
    raw = _normalize(title + " " + kind)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _token_similarity(a: str, b: str) -> float:
    """Jaccard similarity on word token sets."""
    ta = set(_normalize(a).split())
    tb = set(_normalize(b).split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ---------------------------------------------------------------------------
# Registry I/O (JSONL, file-locked for concurrent safety)
# ---------------------------------------------------------------------------

def _ensure_data_dir() -> None:
    _DATA.mkdir(parents=True, exist_ok=True)


def _load_registry() -> list[IdentityRecord]:
    _ensure_data_dir()
    if not _REGISTRY_FILE.exists():
        return []
    records = []
    with _REGISTRY_FILE.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(IdentityRecord.from_dict(json.loads(line)))
                except (KeyError, json.JSONDecodeError):
                    pass
    return records


def _save_registry(records: list[IdentityRecord]) -> None:
    _ensure_data_dir()
    with _REGISTRY_FILE.open("w") as f:
        for r in records:
            f.write(json.dumps(r.to_dict()) + "\n")


def _append_review_queue(entry: dict) -> None:
    _ensure_data_dir()
    with _REVIEW_QUEUE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Core resolver
# ---------------------------------------------------------------------------

def resolve_or_create_workflow(
    title: str,
    kind: str,
    rationale: str = "",
    owner: str = "rick",
    db_workflow_id: str = "",
    source_path: str = "",
    dry_run: bool = False,
) -> ResolveResult:
    """
    Resolve or create a canonical workflow identity.

    Returns ResolveResult with action:
      "reuse"        — exact fingerprint match found; use existing uid
      "needs_review" — fuzzy match found; do NOT create, enqueued for review
      "create"       — no match; new record written (unless dry_run=True)
    """
    _ensure_data_dir()
    lock_path = str(_LOCK_FILE)

    # Use a file lock so concurrent callers don't double-write
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return _resolve_locked(title, kind, rationale, owner, db_workflow_id, source_path, dry_run)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _resolve_locked(
    title: str,
    kind: str,
    rationale: str,
    owner: str,
    db_workflow_id: str,
    source_path: str,
    dry_run: bool,
) -> ResolveResult:
    records = _load_registry()
    fp = compute_fingerprint(title, kind)
    now = _now_iso()

    # 1. Exact fingerprint match
    for rec in records:
        if rec.objective_fingerprint == fp:
            # Update last_seen_at and optionally add source_path
            rec.last_seen_at = now
            if source_path and source_path not in rec.source_paths:
                rec.source_paths.append(source_path)
            if db_workflow_id and not rec.db_workflow_id:
                rec.db_workflow_id = db_workflow_id
            if not dry_run:
                _save_registry(records)
            return ResolveResult(
                action="reuse",
                workflow_uid=rec.workflow_uid,
                record=rec,
                reason=f"Exact fingerprint match: {fp}",
            )

    # 2. Fuzzy match check (title similarity across same kind)
    same_kind = [r for r in records if r.kind == kind]
    fuzzy_matches = []
    for rec in same_kind:
        sim = _token_similarity(title, rec.canonical_title)
        if sim >= FUZZY_THRESHOLD:
            fuzzy_matches.append((sim, rec))

    if fuzzy_matches:
        fuzzy_matches.sort(key=lambda x: x[0], reverse=True)
        candidates = [r for _, r in fuzzy_matches]
        review_entry = {
            "timestamp": now,
            "action": "needs_review",
            "proposed_title": title,
            "proposed_kind": kind,
            "proposed_rationale": rationale,
            "top_candidate_uid": candidates[0].workflow_uid,
            "top_candidate_title": candidates[0].canonical_title,
            "similarity": fuzzy_matches[0][0],
            "db_workflow_id": db_workflow_id,
        }
        if not dry_run:
            _append_review_queue(review_entry)
        return ResolveResult(
            action="needs_review",
            candidates=candidates,
            reason=f"Fuzzy match (sim={fuzzy_matches[0][0]:.2f}) with '{candidates[0].canonical_title}'",
        )

    # 3. No match — create new
    new_uid = f"wf_{uuid.uuid4().hex[:12]}"
    new_record = IdentityRecord(
        workflow_uid=new_uid,
        objective_fingerprint=fp,
        canonical_title=title[:80],
        kind=kind,
        rationale=rationale[:200],
        status="queued",
        owner=owner,
        created_at=now,
        last_seen_at=now,
        aliases=[],
        source_paths=[source_path] if source_path else [],
        supersedes=[],
        db_workflow_id=db_workflow_id,
    )
    if not dry_run:
        records.append(new_record)
        _save_registry(records)
    return ResolveResult(
        action="create",
        workflow_uid=new_uid,
        record=new_record,
        reason="No match found; new identity created.",
    )


# ---------------------------------------------------------------------------
# Registry management utilities
# ---------------------------------------------------------------------------

def get_record_by_uid(workflow_uid: str) -> Optional[IdentityRecord]:
    for rec in _load_registry():
        if rec.workflow_uid == workflow_uid:
            return rec
    return None


def mark_superseded(workflow_uid: str, superseded_by: str) -> bool:
    """Mark a workflow as superseded by another uid."""
    records = _load_registry()
    for rec in records:
        if rec.workflow_uid == workflow_uid:
            rec.status = "superseded"
            rec.last_seen_at = _now_iso()
            if superseded_by not in rec.supersedes:
                rec.supersedes.append(superseded_by)
            _save_registry(records)
            return True
    return False


def add_alias(workflow_uid: str, alias: str) -> bool:
    records = _load_registry()
    for rec in records:
        if rec.workflow_uid == workflow_uid:
            if alias not in rec.aliases:
                rec.aliases.append(alias)
                rec.last_seen_at = _now_iso()
                _save_registry(records)
            return True
    return False


def list_all(status_filter: Optional[str] = None) -> list[IdentityRecord]:
    records = _load_registry()
    if status_filter:
        return [r for r in records if r.status == status_filter]
    return records


def registry_stats() -> dict:
    records = _load_registry()
    from collections import Counter
    status_counts = Counter(r.status for r in records)
    kind_counts = Counter(r.kind for r in records)
    return {
        "total": len(records),
        "by_status": dict(status_counts),
        "by_kind": dict(kind_counts),
    }


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Workflow Identity Resolver CLI")
    sub = parser.add_subparsers(dest="cmd")

    resolve_p = sub.add_parser("resolve", help="Resolve or create workflow identity")
    resolve_p.add_argument("--title", required=True)
    resolve_p.add_argument("--kind", required=True)
    resolve_p.add_argument("--rationale", default="")
    resolve_p.add_argument("--dry-run", action="store_true")

    sub.add_parser("stats", help="Show registry stats")
    sub.add_parser("list", help="List all registry entries")

    supersede_p = sub.add_parser("supersede", help="Mark workflow as superseded")
    supersede_p.add_argument("--uid", required=True)
    supersede_p.add_argument("--by", required=True)

    alias_p = sub.add_parser("alias", help="Add alias to workflow")
    alias_p.add_argument("--uid", required=True)
    alias_p.add_argument("--alias", required=True)

    args = parser.parse_args()

    if args.cmd == "resolve":
        result = resolve_or_create_workflow(
            title=args.title,
            kind=args.kind,
            rationale=args.rationale,
            dry_run=args.dry_run,
        )
        print(json.dumps({
            "action": result.action,
            "workflow_uid": result.workflow_uid,
            "reason": result.reason,
            "candidates": [c.workflow_uid for c in result.candidates],
        }, indent=2))
        if result.action == "needs_review":
            sys.exit(2)

    elif args.cmd == "stats":
        print(json.dumps(registry_stats(), indent=2))

    elif args.cmd == "list":
        for rec in list_all():
            print(json.dumps(rec.to_dict()))

    elif args.cmd == "supersede":
        ok = mark_superseded(args.uid, args.by)
        print("ok" if ok else "uid not found", file=sys.stderr if not ok else sys.stdout)
        sys.exit(0 if ok else 1)

    elif args.cmd == "alias":
        ok = add_alias(args.uid, args.alias)
        print("ok" if ok else "uid not found", file=sys.stderr if not ok else sys.stdout)
        sys.exit(0 if ok else 1)

    else:
        parser.print_help()
        sys.exit(1)
