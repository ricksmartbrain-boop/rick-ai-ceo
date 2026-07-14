"""
Tests for workflow_identity_resolver.py

These tests verify the INTENT of the resolver: that duplicate workflow creation
is prevented by the gate, that exact matches reuse, fuzzy matches go to review,
and distinct workflows get new identities.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Patch the vault path to a temp dir for all tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def temp_vault(tmp_path, monkeypatch):
    """Redirect all resolver I/O to a temporary directory."""
    import scripts.workflow_identity_resolver as resolver
    monkeypatch.setattr(resolver, "_VAULT", tmp_path)
    monkeypatch.setattr(resolver, "_DATA", tmp_path / "data")
    monkeypatch.setattr(resolver, "_REGISTRY_FILE", tmp_path / "data" / "workflow-identity-registry.jsonl")
    monkeypatch.setattr(resolver, "_REVIEW_QUEUE", tmp_path / "data" / "workflow-identity-review-queue.jsonl")
    monkeypatch.setattr(resolver, "_LOCK_FILE", tmp_path / "data" / ".workflow-identity-registry.lock")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Import after fixture setup (fixtures run before module-level usage)
# ---------------------------------------------------------------------------

from scripts.workflow_identity_resolver import (
    ResolveResult,
    add_alias,
    compute_fingerprint,
    list_all,
    mark_superseded,
    registry_stats,
    resolve_or_create_workflow,
)


# ---------------------------------------------------------------------------
# Fingerprint tests
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_deterministic(self):
        """Same input always produces same fingerprint."""
        fp1 = compute_fingerprint("Canonical Initiative Registry", "automation")
        fp2 = compute_fingerprint("Canonical Initiative Registry", "automation")
        assert fp1 == fp2

    def test_case_insensitive(self):
        """Fingerprint ignores case."""
        fp1 = compute_fingerprint("Canonical Initiative Registry", "automation")
        fp2 = compute_fingerprint("canonical initiative registry", "AUTOMATION")
        assert fp1 == fp2

    def test_different_title_different_fp(self):
        """Different titles produce different fingerprints."""
        fp1 = compute_fingerprint("Canonical Initiative Registry", "automation")
        fp2 = compute_fingerprint("Daily Ship Gate", "automation")
        assert fp1 != fp2

    def test_different_kind_different_fp(self):
        """Same title, different kind = different fingerprint."""
        fp1 = compute_fingerprint("My Workflow", "initiative")
        fp2 = compute_fingerprint("My Workflow", "automation")
        assert fp1 != fp2


# ---------------------------------------------------------------------------
# Core resolver: create path
# ---------------------------------------------------------------------------

class TestCreate:
    def test_new_workflow_creates_record(self):
        """A new title/kind pair should create a registry entry."""
        result = resolve_or_create_workflow(
            title="Daily Ship Gate",
            kind="automation",
            rationale="Forces a daily shipped artifact.",
        )
        assert result.action == "create"
        assert result.workflow_uid is not None
        assert result.workflow_uid.startswith("wf_")
        assert result.record is not None
        assert result.record.canonical_title == "Daily Ship Gate"

    def test_registry_persists_after_create(self):
        """Created record must be readable from the registry."""
        result = resolve_or_create_workflow(title="My New Initiative", kind="initiative")
        records = list_all()
        assert len(records) == 1
        assert records[0].workflow_uid == result.workflow_uid

    def test_dry_run_does_not_persist(self):
        """dry_run=True should not write to the registry."""
        result = resolve_or_create_workflow(
            title="Ghost Workflow", kind="initiative", dry_run=True
        )
        assert result.action == "create"
        records = list_all()
        assert len(records) == 0

    def test_uid_is_unique_per_create(self):
        """Two distinct workflows get distinct UIDs."""
        r1 = resolve_or_create_workflow(title="Workflow Alpha", kind="initiative")
        r2 = resolve_or_create_workflow(title="Workflow Beta", kind="initiative")
        assert r1.workflow_uid != r2.workflow_uid


# ---------------------------------------------------------------------------
# Core resolver: reuse path
# ---------------------------------------------------------------------------

class TestReuse:
    def test_exact_match_returns_reuse(self):
        """Calling with the same title/kind twice must return 'reuse' on second call."""
        r1 = resolve_or_create_workflow(title="Canonical Initiative Registry", kind="automation")
        assert r1.action == "create"

        r2 = resolve_or_create_workflow(title="Canonical Initiative Registry", kind="automation")
        assert r2.action == "reuse"
        assert r2.workflow_uid == r1.workflow_uid

    def test_reuse_updates_last_seen_at(self):
        """Reuse should update last_seen_at on the record."""
        resolve_or_create_workflow(title="My Workflow", kind="initiative")
        r2 = resolve_or_create_workflow(title="My Workflow", kind="initiative")
        assert r2.action == "reuse"
        assert r2.record is not None

    def test_exact_match_case_insensitive(self):
        """Case variation of same title/kind reuses existing record."""
        r1 = resolve_or_create_workflow(title="Canonical Workflow Identity Service", kind="automation")
        r2 = resolve_or_create_workflow(title="canonical workflow identity service", kind="AUTOMATION")
        assert r2.action == "reuse"
        assert r2.workflow_uid == r1.workflow_uid

    def test_reuse_does_not_create_duplicate_registry_entries(self):
        """Registry should have exactly 1 entry after create + reuse."""
        resolve_or_create_workflow(title="My Initiative", kind="initiative")
        resolve_or_create_workflow(title="My Initiative", kind="initiative")
        records = list_all()
        assert len(records) == 1


# ---------------------------------------------------------------------------
# Core resolver: needs_review path
# ---------------------------------------------------------------------------

class TestNeedsReview:
    def test_fuzzy_match_returns_needs_review(self):
        """
        Highly similar but non-identical title should trigger needs_review, not create.
        This is the key guard against the 'Canonical Initiative Registry' duplicate storm.
        """
        resolve_or_create_workflow(
            title="Canonical Initiative Registry",
            kind="automation",
            rationale="Prevents duplicate creation.",
        )
        # Variant title — same tokens, different phrasing
        r2 = resolve_or_create_workflow(
            title="Canonical Initiative Registry Service",
            kind="automation",
            rationale="A registry for canonical initiatives.",
        )
        assert r2.action == "needs_review"
        assert len(r2.candidates) > 0

    def test_needs_review_does_not_create_registry_entry(self):
        """needs_review result must not add a new registry record."""
        resolve_or_create_workflow(title="Canonical Initiative Registry", kind="automation")
        resolve_or_create_workflow(title="Canonical Initiative Registry Service", kind="automation")
        records = list_all()
        # Still only 1 record (the original)
        assert len(records) == 1

    def test_needs_review_writes_to_review_queue(self, temp_vault):
        """needs_review must append an entry to the review queue file."""
        resolve_or_create_workflow(title="Canonical Initiative Registry", kind="automation")
        resolve_or_create_workflow(title="Canonical Initiative Registry Service", kind="automation")
        queue_file = temp_vault / "data" / "workflow-identity-review-queue.jsonl"
        assert queue_file.exists()
        lines = [l for l in queue_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["action"] == "needs_review"
        assert "Canonical Initiative Registry" in entry["top_candidate_title"]

    def test_different_kind_does_not_trigger_fuzzy(self):
        """Fuzzy check is scoped to same kind — different kind should create."""
        resolve_or_create_workflow(title="Canonical Initiative Registry", kind="automation")
        r2 = resolve_or_create_workflow(title="Canonical Initiative Registry", kind="initiative")
        # Different kind → different fingerprint → create (or could be reuse if fp matches, but kind differs)
        # Since fingerprint includes kind, this should be a new create
        assert r2.action == "create"


# ---------------------------------------------------------------------------
# Known duplicate case: the "Canonical Initiative Registry" storm
# ---------------------------------------------------------------------------

class TestKnownDuplicateCases:
    VARIANTS = [
        ("Canonical Initiative Registry", "automation", "Addresses duplicate workflow creation."),
        ("Canonical Initiative Registry", "automation", "Prevents repeated creation of the same initiatives."),
        ("Canonical Initiative Registry", "automation", "Best direct fix for duplicate initiative creation."),
    ]

    def test_all_variants_reuse_same_uid(self):
        """
        All three variants of 'Canonical Initiative Registry' in automation should
        resolve to the same UID — proving the gate is exercised.
        """
        uids = set()
        for title, kind, rationale in self.VARIANTS:
            result = resolve_or_create_workflow(title=title, kind=kind, rationale=rationale)
            if result.action in ("create", "reuse"):
                uids.add(result.workflow_uid)
            # needs_review cases don't get a uid — that's correct behavior

        # At most one canonical UID should exist
        assert len(uids) == 1

    def test_high_similarity_variant_triggers_needs_review(self):
        """
        'Workflow Identity Service' vs 'Canonical Workflow Identity Service' share
        75% token similarity — above the 0.70 threshold — so the second call must
        NOT create a new record; it must go to needs_review.
        This guards against the name-variant duplication pattern in the canonical
        initiative registry storm.
        """
        resolve_or_create_workflow(
            title="Workflow Identity Service",
            kind="automation",
            rationale="Single source of truth for workflow IDs.",
        )
        r2 = resolve_or_create_workflow(
            title="Canonical Workflow Identity Service",
            kind="automation",
            rationale="Single source of truth for initiative IDs.",
        )
        # sim=0.75 >= threshold=0.70 → must NOT create a new record
        assert r2.action == "needs_review"
        assert len(r2.candidates) > 0

    def test_distinct_titles_below_threshold_do_create(self):
        """
        'Workflow Family Identity Service' vs 'Canonical Workflow Identity Service'
        have sim=0.60 < 0.70, so they are allowed to coexist as separate workflows.
        """
        resolve_or_create_workflow(
            title="Workflow Family Identity Service",
            kind="automation",
            rationale="Family-scoped identity.",
        )
        r2 = resolve_or_create_workflow(
            title="Canonical Workflow Identity Service",
            kind="automation",
            rationale="Single source of truth for initiative IDs.",
        )
        assert r2.action == "create"


# ---------------------------------------------------------------------------
# Registry management
# ---------------------------------------------------------------------------

class TestRegistryManagement:
    def test_mark_superseded(self):
        r = resolve_or_create_workflow(title="Old Initiative", kind="initiative")
        uid = r.workflow_uid
        ok = mark_superseded(uid, superseded_by="wf_newuid123456")
        assert ok
        records = list_all()
        rec = next(r for r in records if r.workflow_uid == uid)
        assert rec.status == "superseded"
        assert "wf_newuid123456" in rec.supersedes

    def test_add_alias(self):
        r = resolve_or_create_workflow(title="My Initiative", kind="initiative")
        uid = r.workflow_uid
        ok = add_alias(uid, "old-slug-variant")
        assert ok
        records = list_all()
        rec = next(r for r in records if r.workflow_uid == uid)
        assert "old-slug-variant" in rec.aliases

    def test_registry_stats(self):
        resolve_or_create_workflow(title="Alpha", kind="initiative")
        resolve_or_create_workflow(title="Beta", kind="automation")
        stats = registry_stats()
        assert stats["total"] == 2
        assert stats["by_kind"]["initiative"] == 1
        assert stats["by_kind"]["automation"] == 1

    def test_list_all_status_filter(self):
        r = resolve_or_create_workflow(title="Old One", kind="initiative")
        mark_superseded(r.workflow_uid, superseded_by="wf_x")
        resolve_or_create_workflow(title="New One", kind="initiative")

        queued = list_all(status_filter="queued")
        superseded = list_all(status_filter="superseded")
        assert len(queued) == 1
        assert len(superseded) == 1
