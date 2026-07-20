"""Tests for memory system in runtime/context.py — retrieval scoring, context pack
assembly, and the memory index rebuild script.

Surface note (2026-07-19 runtime is canonical): the module-level memory-index /
haystack caches were removed — load_memory_index() reads fresh every call so a
rebuilt index is visible immediately. The only cache left is _context_pack_cache
(TTL-bound by _CONTEXT_PACK_TTL). build_context_pack also counts open approvals
from the runtime DB (open_approval_count — the approvals.md mirror went stale
and under-reported, 2026-07-14), so fixture DBs must carry an approvals table.
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class FakeRow(dict):
    """Mimics sqlite3.Row for testing context functions."""

    def __getitem__(self, key):
        return dict.__getitem__(self, key)


class MemoryContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.env_backup = os.environ.copy()
        os.environ["RICK_DATA_ROOT"] = self.tempdir.name
        # conftest.py points these at the session-wide hermetic tree; rebind
        # them to this test's tempdir so the fixture index below is the one
        # the reloaded module actually reads.
        os.environ["RICK_MEMORY_DIR"] = str(Path(self.tempdir.name) / "memory")
        os.environ["RICK_MEMORY_INDEX_FILE"] = str(Path(self.tempdir.name) / "control" / "memory-index.json")
        os.environ["RICK_DREAMS_FILE"] = str(Path(self.tempdir.name) / "DREAMS.md")

        # Create required directories
        (Path(self.tempdir.name) / "memory").mkdir(parents=True, exist_ok=True)
        (Path(self.tempdir.name) / "control").mkdir(parents=True, exist_ok=True)
        (Path(self.tempdir.name) / "revenue").mkdir(parents=True, exist_ok=True)
        (Path(self.tempdir.name) / "scorecards").mkdir(parents=True, exist_ok=True)

        # Write a memory index with mixed tiers and projects
        self.index_path = Path(self.tempdir.name) / "control" / "memory-index.json"
        self.index_data = {
            "generated_at": "2026-03-13T04:00:00",
            "root": self.tempdir.name,
            "counts": {"entries": 4, "tiers": {"hot": 2, "warm": 1, "cold": 1}, "types": {"note": 4}, "projects": {"alpha": 2}},
            "entries": [
                {"path": "projects/alpha/summary.md", "title": "Alpha Summary", "type": "note", "project": "alpha", "tier": "hot", "modified_at": "2026-03-12T10:00:00", "created_at": "2026-03-10T10:00:00", "last_accessed_at": "", "preview": "Launch the alpha product for agencies", "tags": ["launch", "agency"], "wikilinks": ["control/playbook"]},
                {"path": "projects/alpha/research.md", "title": "Alpha Research", "type": "note", "project": "alpha", "tier": "warm", "modified_at": "2026-02-20T10:00:00", "created_at": "2026-02-18T10:00:00", "last_accessed_at": "", "preview": "Market research for alpha product", "tags": ["research"], "wikilinks": []},
                {"path": "memory/2026-03-13.md", "title": "Daily Notes", "type": "daily", "project": "", "tier": "hot", "modified_at": "2026-03-13T08:00:00", "created_at": "2026-03-13T06:00:00", "last_accessed_at": "", "preview": "Working on beta launch today", "tags": ["daily"], "wikilinks": []},
                {"path": "decisions/old-decision.md", "title": "Old Decision", "type": "decision", "project": "", "tier": "cold", "modified_at": "2025-12-01T10:00:00", "created_at": "2025-11-01T10:00:00", "last_accessed_at": "", "preview": "Archived decision about pricing", "tags": ["pricing"], "wikilinks": []},
            ],
            "hot_entries": [],
        }
        self.index_path.write_text(json.dumps(self.index_data), encoding="utf-8")

        import runtime.context as ctx
        self.ctx = importlib.reload(ctx)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env_backup)
        self.ctx._context_pack_cache.clear()
        # Rebind module constants to the restored (hermetic conftest) env so
        # later test modules don't read paths inside this deleted tempdir.
        importlib.reload(self.ctx)
        self.tempdir.cleanup()

    def _make_workflow_row(self, title="Alpha Launch", slug="alpha-launch", project="alpha", context_json='{"idea": "launch alpha product", "project": "alpha"}'):
        return FakeRow({"id": "wf-1", "kind": "info_product_launch", "title": title, "slug": slug, "lane": "product-lane", "status": "active", "stage": "context_pack", "project": project, "context_json": context_json})

    def _make_pack_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE artifacts (kind TEXT, title TEXT, path TEXT, created_at TEXT, workflow_id TEXT)")
        conn.execute("CREATE TABLE jobs (lane TEXT, status TEXT)")
        # build_context_pack counts open approvals straight from the DB —
        # the approvals table is now a hard requirement of pack assembly.
        conn.execute("CREATE TABLE approvals (id TEXT, status TEXT)")
        conn.execute("CREATE TABLE outcomes (step_name TEXT, route TEXT, outcome_type TEXT, cost_usd REAL, duration_seconds REAL, model_used TEXT, created_at TEXT)")
        return conn

    # --- Index loading: fresh reads, graceful degradation ---

    def test_load_memory_index_reads_fresh_on_change(self) -> None:
        """A rebuilt index must be visible immediately — the old module-level
        cache served stale entries until process restart, which is why it was
        removed. Workflows act on what the vault knows NOW."""
        idx1 = self.ctx.load_memory_index()
        self.assertEqual(idx1["counts"]["entries"], 4)

        self.index_path.write_text(json.dumps({"counts": {"entries": 99}, "entries": []}), encoding="utf-8")

        idx2 = self.ctx.load_memory_index()
        self.assertEqual(idx2["counts"]["entries"], 99)

    def test_load_memory_index_malformed_returns_empty(self) -> None:
        """A corrupt index must degrade to {} — pack assembly runs mid-workflow
        and a crash here would kill the step, not just the memory section."""
        self.index_path.write_text("NOT JSON {", encoding="utf-8")
        self.assertEqual(self.ctx.load_memory_index(), {})

    # --- Context pack cache: TTL-bound ---

    def test_context_pack_cache_expires_after_ttl(self) -> None:
        """Business state in the pack (open approvals, revenue) goes stale;
        the cache may serve it only within _CONTEXT_PACK_TTL, never beyond."""
        conn = self._make_pack_db()
        row = self._make_workflow_row()
        pack1 = self.ctx.build_context_pack(conn, row, step_name="publish_newsletter")

        cached_at, cached_pack = self.ctx._context_pack_cache["wf-1"]
        self.ctx._context_pack_cache["wf-1"] = (cached_at - self.ctx._CONTEXT_PACK_TTL - 1, cached_pack)

        pack2 = self.ctx.build_context_pack(conn, row, step_name="publish_newsletter")
        self.assertIsNot(pack2, pack1)
        # The rebuilt pack replaces the expired cache entry
        self.assertIs(self.ctx._context_pack_cache["wf-1"][1], pack2)
        conn.close()

    # --- Error guards ---

    def test_related_memory_notes_malformed_context_json(self) -> None:
        """Malformed context_json should not crash related_memory_notes."""
        row = self._make_workflow_row(context_json="NOT VALID JSON")
        result = self.ctx.related_memory_notes(row)
        self.assertIsInstance(result, list)

    # --- Scoring tests ---

    def test_related_memory_notes_scoring(self) -> None:
        """Token matching scores entries by relevance with tier and project bonuses."""
        row = self._make_workflow_row()
        results = self.ctx.related_memory_notes(row)

        self.assertGreater(len(results), 0)
        # Alpha project entries should rank highest (token match + project bonus)
        self.assertEqual(results[0]["project"], "alpha")

    def test_related_memory_notes_tier_bonus(self) -> None:
        """Hot entries outrank cold entries with equal token matches and age —
        the tier bonus is what keeps working memory ahead of the archive."""
        entries = []
        for tier in ("cold", "hot"):  # cold listed first: order must come from score, not insertion
            entries.append({
                "path": f"notes/{tier}.md", "title": f"Pricing {tier}", "type": "note",
                "project": "", "tier": tier, "modified_at": "2026-03-12T10:00:00",
                "created_at": "2026-03-10T10:00:00", "last_accessed_at": "",
                "preview": "pricing decision", "tags": [], "wikilinks": [],
            })
        self.index_path.write_text(json.dumps({"counts": {"entries": 2}, "entries": entries}), encoding="utf-8")

        row = self._make_workflow_row(title="pricing decision", slug="pricing", project="", context_json='{"idea": "pricing"}')
        results = self.ctx.related_memory_notes(row)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["path"], "notes/hot.md")

    def test_related_memory_notes_empty_index(self) -> None:
        """Empty memory index returns empty list."""
        self.index_path.write_text(json.dumps({"entries": []}), encoding="utf-8")
        row = self._make_workflow_row()
        self.assertEqual(self.ctx.related_memory_notes(row), [])

    def test_related_memory_notes_respects_limit(self) -> None:
        """Limit parameter caps results."""
        row = self._make_workflow_row()
        results = self.ctx.related_memory_notes(row, limit=1)
        self.assertLessEqual(len(results), 1)

    def test_related_memory_matching_is_case_insensitive(self) -> None:
        """Workflow titles arrive in arbitrary case; retrieval must not miss
        an entry because the vault note was titled differently."""
        row = self._make_workflow_row(title="ALPHA LAUNCH", slug="ALPHA-LAUNCH")
        results = self.ctx.related_memory_notes(row)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["project"], "alpha")

    # --- memory_index_summary ---

    def test_memory_index_summary_extracts_counts(self) -> None:
        """memory_index_summary returns correct tier counts and flags staleness."""
        summary = self.ctx.memory_index_summary()
        self.assertEqual(summary["entries"], 4)
        self.assertEqual(summary["tiers"]["hot"], 2)
        self.assertEqual(summary["tiers"]["warm"], 1)
        self.assertEqual(summary["tiers"]["cold"], 1)
        self.assertEqual(summary["generated_at"], "2026-03-13T04:00:00")
        # generated_at is >24h old — the summary must say so, or Rick briefs
        # workflows against an index nobody has rebuilt in months.
        self.assertTrue(summary["stale"])

    def test_memory_index_summary_missing_file(self) -> None:
        """Missing index file returns zeroed summary."""
        self.index_path.unlink()
        summary = self.ctx.memory_index_summary()
        self.assertEqual(summary["entries"], 0)

    # --- recent_memory ---

    def test_recent_memory_returns_last_n(self) -> None:
        """recent_memory returns the last N files with titles."""
        mem_dir = Path(self.tempdir.name) / "memory"
        (mem_dir / "2026-03-11.md").write_text("# Day One\nContent\n", encoding="utf-8")
        (mem_dir / "2026-03-12.md").write_text("# Day Two\nContent\n", encoding="utf-8")
        (mem_dir / "2026-03-13.md").write_text("# Day Three\nContent\n", encoding="utf-8")

        results = self.ctx.recent_memory(limit=2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Day Two")
        self.assertEqual(results[1]["title"], "Day Three")

    def test_recent_memory_empty_dir(self) -> None:
        """Empty memory dir returns empty list."""
        results = self.ctx.recent_memory()
        self.assertEqual(results, [])

    # --- Ranking keeps hot memory ahead of the archive ---

    def test_hot_entries_outrank_cold_within_limit(self) -> None:
        """When plenty of hot entries match, the cold archive entry must fall
        outside the limit — retrieval budget goes to working memory first."""
        hot_entries = [
            {"path": f"projects/alpha/note{i}.md", "title": f"Alpha Note {i}", "type": "note",
             "project": "alpha", "tier": "hot", "modified_at": "2026-03-12T10:00:00",
             "created_at": "2026-03-10T10:00:00", "last_accessed_at": "",
             "preview": "alpha launch agency product info", "tags": ["alpha", "launch", "agency", "product"],
             "wikilinks": []}
            for i in range(8)
        ]
        cold_entry = {"path": "archive/old.md", "title": "Old Alpha Note", "type": "note",
                      "project": "", "tier": "cold", "modified_at": "2025-01-01T10:00:00",
                      "created_at": "2025-01-01T10:00:00", "last_accessed_at": "",
                      "preview": "alpha launch", "tags": ["alpha"], "wikilinks": []}
        big_index = {
            "counts": {"entries": 9, "tiers": {"hot": 8, "cold": 1}},
            "entries": [cold_entry] + hot_entries,
        }
        self.index_path.write_text(json.dumps(big_index), encoding="utf-8")

        row = self._make_workflow_row()
        results = self.ctx.related_memory_notes(row, limit=6)
        self.assertEqual(len(results), 6)
        for r in results:
            self.assertEqual(r["tier"], "hot")

    # --- Context pack assembly ---

    def test_conditional_context_publish_step_excludes_related_memory(self) -> None:
        """Publish steps should exclude related_memory from context pack."""
        conn = self._make_pack_db()
        row = self._make_workflow_row()
        pack = self.ctx.build_context_pack(conn, row, step_name="publish_newsletter")
        self.assertEqual(pack["related_memory"], [])
        conn.close()

    def test_context_pack_counts_open_approvals_from_db(self) -> None:
        """open_approvals must come from the runtime DB, not the approvals.md
        mirror — the mirror went stale and under-reported (2026-07-14). Only
        rows with status='open' count."""
        conn = self._make_pack_db()
        conn.execute("INSERT INTO approvals VALUES ('apr_1', 'open')")
        conn.execute("INSERT INTO approvals VALUES ('apr_2', 'open')")
        conn.execute("INSERT INTO approvals VALUES ('apr_3', 'approved')")
        row = self._make_workflow_row()
        pack = self.ctx.build_context_pack(conn, row, step_name="publish_newsletter")
        self.assertEqual(pack["business"]["open_approvals"], 2)
        conn.close()

    def test_context_pack_cache_returns_cached_on_second_call(self) -> None:
        """Second call to build_context_pack with same workflow returns cached result."""
        conn = self._make_pack_db()
        row = self._make_workflow_row()
        pack1 = self.ctx.build_context_pack(conn, row, step_name="context_pack")
        pack2 = self.ctx.build_context_pack(conn, row, step_name="context_pack")
        # Same object returned from cache
        self.assertEqual(pack1["generated_at"], pack2["generated_at"])
        self.assertIs(pack1, pack2)
        conn.close()


class MemoryIndexRebuildTests(unittest.TestCase):
    """Tests for rebuild-memory-index.py: full rebuild semantics and access log
    rotation. The current script has no incremental mode — build_index() is a
    full vault scan, which is what makes prune/pickup correctness trivial to
    guarantee."""

    def setUp(self) -> None:
        import importlib.util

        self.tempdir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tempdir.name)
        self.env_backup = os.environ.copy()
        os.environ.update({
            "RICK_DATA_ROOT": str(self.data_root),
            "RICK_MEMORY_INDEX_FILE": str(self.data_root / "control" / "memory-index.json"),
            "RICK_MEMORY_ACCESS_LOG_FILE": str(self.data_root / "operations" / "memory-access.jsonl"),
            "RICK_MEMORY_OVERVIEW_FILE": str(self.data_root / "dashboards" / "memory-overview.md"),
        })
        for d in ("memory", "control", "operations", "dashboards", "projects/test-proj"):
            (self.data_root / d).mkdir(parents=True, exist_ok=True)

        spec = importlib.util.spec_from_file_location(
            "rick_memory_index_ctx_test",
            ROOT_DIR / "skills" / "obsidian-memory" / "scripts" / "rebuild-memory-index.py",
        )
        self.mod = importlib.util.module_from_spec(spec)
        sys.modules["rick_memory_index_ctx_test"] = self.mod
        spec.loader.exec_module(self.mod)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env_backup)
        self.tempdir.cleanup()

    def _write_file(self, rel_path: str, content: str, days_ago: int = 0) -> Path:
        path = self.data_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        ts = self.mod.now().timestamp() - days_ago * 86400
        os.utime(path, (ts, ts))
        return path

    def test_rebuild_is_stable_when_vault_unchanged(self) -> None:
        """Two rebuilds over an unchanged vault must index the same entries —
        index churn would invalidate retrieval scores between runs for no reason."""
        self._write_file("memory/2026-03-12.md", "# Day\nContent\n", 1)
        self._write_file("projects/test-proj/summary.md", "---\ntype: project\n---\n# Test\nSummary\n", 2)

        first = self.mod.build_index()
        self.mod.write_index(first)
        self.assertEqual(first["counts"]["entries"], 2)

        second = self.mod.build_index()
        self.assertEqual(second["counts"]["entries"], 2)
        first_paths = sorted(e["path"] for e in first["entries"])
        second_paths = sorted(e["path"] for e in second["entries"])
        self.assertEqual(first_paths, second_paths)

    def test_rebuild_picks_up_new_files(self) -> None:
        """New vault files must be indexed on the next rebuild — otherwise new
        memory is invisible to retrieval."""
        self._write_file("memory/2026-03-12.md", "# Day\nContent\n", 1)
        self.mod.write_index(self.mod.build_index())

        self._write_file("memory/2026-03-13.md", "# New Day\nNew content\n", 0)
        rebuilt = self.mod.build_index()
        self.assertEqual(rebuilt["counts"]["entries"], 2)

    def test_rebuild_prunes_deleted_files(self) -> None:
        """Deleted files must drop out on rebuild — a ghost entry would feed
        context packs paths that 404 when a workflow tries to read them."""
        path = self._write_file("memory/2026-03-12.md", "# Day\nContent\n", 1)
        self.mod.write_index(self.mod.build_index())

        path.unlink()
        rebuilt = self.mod.build_index()
        self.assertEqual(rebuilt["counts"]["entries"], 0)

    def test_access_log_rotation(self) -> None:
        """Entries older than max_age_days are pruned; recent entries survive;
        unparseable lines are kept — rotation must never destroy data it
        cannot positively identify as expired."""
        log_path = self.data_root / "operations" / "memory-access.jsonl"
        from datetime import timedelta
        old_ts = self.mod.iso_timestamp(self.mod.now() - timedelta(days=45))
        new_ts = self.mod.iso_timestamp(self.mod.now() - timedelta(days=5))
        log_path.write_text(
            json.dumps({"timestamp": old_ts, "path": "old-note.md"}) + "\n"
            + json.dumps({"timestamp": new_ts, "path": "new-note.md"}) + "\n"
            + "not-json garbage line\n",
            encoding="utf-8",
        )

        self.mod.rotate_access_log(max_age_days=30)

        remaining = log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(remaining), 2)
        self.assertIn("new-note.md", remaining[0])
        self.assertNotIn("old-note.md", "\n".join(remaining))
        self.assertEqual(remaining[1], "not-json garbage line")


if __name__ == "__main__":
    unittest.main()
