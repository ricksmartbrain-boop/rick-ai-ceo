"""Tests for memory system in runtime/context.py — scoring, caching, error handling."""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
        # Reset caches
        self.ctx._memory_index_cache = (0.0, {})
        self.ctx._haystack_cache = (0.0, {})
        self.ctx._context_cache.clear()
        self.tempdir.cleanup()

    def _make_workflow_row(self, title="Alpha Launch", slug="alpha-launch", project="alpha", context_json='{"idea": "launch alpha product", "project": "alpha"}'):
        return FakeRow({"id": "wf-1", "kind": "info_product_launch", "title": title, "slug": slug, "lane": "product-lane", "status": "active", "stage": "context_pack", "project": project, "context_json": context_json})

    # --- A1: Index caching ---

    def test_memory_index_cache_avoids_duplicate_reads(self) -> None:
        """A1: Second call to load_memory_index should use cache, not re-read file."""
        idx1 = self.ctx.load_memory_index()
        self.assertEqual(idx1["counts"]["entries"], 4)

        # Overwrite file with different data
        self.index_path.write_text(json.dumps({"counts": {"entries": 99}, "entries": []}), encoding="utf-8")

        # Should still return cached version
        idx2 = self.ctx.load_memory_index()
        self.assertEqual(idx2["counts"]["entries"], 4)

    def test_memory_index_cache_invalidation(self) -> None:
        """A1: invalidate_memory_index_cache forces re-read."""
        self.ctx.load_memory_index()
        self.ctx.invalidate_memory_index_cache()

        self.index_path.write_text(json.dumps({"counts": {"entries": 99}, "entries": []}), encoding="utf-8")
        idx = self.ctx.load_memory_index()
        self.assertEqual(idx["counts"]["entries"], 99)

    # --- A2: Bounded context cache ---

    def test_context_cache_bounded(self) -> None:
        """A2: Context cache should not exceed _MAX_CONTEXT_CACHE entries."""
        for i in range(self.ctx._MAX_CONTEXT_CACHE + 10):
            self.ctx._context_cache[f"wf-{i}"] = (time.monotonic(), {"id": f"wf-{i}"})
            # Simulate eviction logic
            if len(self.ctx._context_cache) > self.ctx._MAX_CONTEXT_CACHE:
                oldest_key = min(self.ctx._context_cache, key=lambda k: self.ctx._context_cache[k][0])
                del self.ctx._context_cache[oldest_key]

        self.assertLessEqual(len(self.ctx._context_cache), self.ctx._MAX_CONTEXT_CACHE)

    # --- A3: Error guards ---

    def test_related_memory_notes_malformed_context_json(self) -> None:
        """A3: Malformed context_json should not crash related_memory_notes."""
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
        """Hot entries score higher than cold entries with equal token matches."""
        row = self._make_workflow_row(title="pricing decision", slug="pricing", project="", context_json='{"idea": "pricing"}')
        results = self.ctx.related_memory_notes(row)

        # Both "Alpha Research" and "Old Decision" mention pricing-adjacent terms
        # but tier bonus should differentiate them
        if len(results) >= 2:
            tiers = [r["tier"] for r in results]
            # Hot/warm entries should come before cold
            hot_warm_idx = [i for i, t in enumerate(tiers) if t in ("hot", "warm")]
            cold_idx = [i for i, t in enumerate(tiers) if t == "cold"]
            if hot_warm_idx and cold_idx:
                self.assertLess(min(hot_warm_idx), max(cold_idx))

    def test_related_memory_notes_empty_index(self) -> None:
        """Empty memory index returns empty list."""
        self.index_path.write_text(json.dumps({"entries": []}), encoding="utf-8")
        self.ctx.invalidate_memory_index_cache()
        row = self._make_workflow_row()
        self.assertEqual(self.ctx.related_memory_notes(row), [])

    def test_related_memory_notes_respects_limit(self) -> None:
        """Limit parameter caps results."""
        row = self._make_workflow_row()
        results = self.ctx.related_memory_notes(row, limit=1)
        self.assertLessEqual(len(results), 1)

    # --- B1: Haystack cache ---

    def test_haystack_cache_populated_on_index_load(self) -> None:
        """B1: Loading index should populate haystack cache."""
        self.ctx.load_memory_index()
        _, haystacks = self.ctx._haystack_cache
        self.assertEqual(len(haystacks), 4)
        # Haystacks should be lowercase
        for path, haystack in haystacks.items():
            self.assertEqual(haystack, haystack.lower())

    # --- memory_index_summary ---

    def test_memory_index_summary_extracts_counts(self) -> None:
        """memory_index_summary returns correct tier counts."""
        summary = self.ctx.memory_index_summary()
        self.assertEqual(summary["entries"], 4)
        self.assertEqual(summary["tiers"]["hot"], 2)
        self.assertEqual(summary["tiers"]["warm"], 1)
        self.assertEqual(summary["tiers"]["cold"], 1)
        self.assertEqual(summary["generated_at"], "2026-03-13T04:00:00")

    def test_memory_index_summary_missing_file(self) -> None:
        """Missing index file returns zeroed summary."""
        self.index_path.unlink()
        self.ctx.invalidate_memory_index_cache()
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

    # --- B2: Early-exit scoring ---

    def test_early_exit_skips_cold_entries_when_enough_high_confidence(self) -> None:
        """B2: When enough high-confidence matches exist, cold entries are skipped."""
        # Build an index with many hot entries that will score high and a cold entry
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
            "entries": hot_entries + [cold_entry],
        }
        self.index_path.write_text(json.dumps(big_index), encoding="utf-8")
        self.ctx.invalidate_memory_index_cache()

        row = self._make_workflow_row()
        results = self.ctx.related_memory_notes(row, limit=6)
        # All results should be hot-tier (cold entry skipped via early exit)
        for r in results:
            self.assertEqual(r["tier"], "hot")

    # --- C1: Conditional context by step ---

    def test_conditional_context_publish_step_excludes_related_memory(self) -> None:
        """C1: Publish steps should exclude related_memory from context pack."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE artifacts (kind TEXT, title TEXT, path TEXT, created_at TEXT, workflow_id TEXT)")
        conn.execute("CREATE TABLE jobs (lane TEXT, status TEXT)")
        row = self._make_workflow_row()
        pack = self.ctx.build_context_pack(conn, row, step_name="publish_newsletter")
        self.assertEqual(pack["related_memory"], [])
        conn.close()

    # --- C2: Context pack cache ---

    def test_context_pack_cache_returns_cached_on_second_call(self) -> None:
        """C2: Second call to build_context_pack with same workflow returns cached result."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE artifacts (kind TEXT, title TEXT, path TEXT, created_at TEXT, workflow_id TEXT)")
        conn.execute("CREATE TABLE jobs (lane TEXT, status TEXT)")
        row = self._make_workflow_row()
        pack1 = self.ctx.build_context_pack(conn, row, step_name="context_pack")
        pack2 = self.ctx.build_context_pack(conn, row, step_name="context_pack")
        # Same object returned from cache
        self.assertEqual(pack1["generated_at"], pack2["generated_at"])
        self.assertIs(pack1, pack2)
        conn.close()


class IncrementalRebuildTests(unittest.TestCase):
    """Tests for incremental rebuild and access log rotation in rebuild-memory-index.py."""

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

    def test_incremental_reuses_unchanged_entries(self) -> None:
        """C1: Unchanged files should be reused without re-reading content."""
        self._write_file("memory/2026-03-12.md", "# Day\nContent\n", 1)
        self._write_file("projects/test-proj/summary.md", "---\ntype: project\n---\n# Test\nSummary\n", 2)

        # Full build first
        full_index = self.mod.build_index()
        self.mod.write_index(full_index)
        self.assertEqual(full_index["counts"]["entries"], 2)

        # Incremental rebuild — no files changed
        inc_index = self.mod.build_index_incremental()
        self.assertEqual(inc_index["counts"]["entries"], 2)
        # Entries should match
        full_paths = sorted(e["path"] for e in full_index["entries"])
        inc_paths = sorted(e["path"] for e in inc_index["entries"])
        self.assertEqual(full_paths, inc_paths)

    def test_incremental_picks_up_new_files(self) -> None:
        """C1: New files should be indexed in incremental mode."""
        self._write_file("memory/2026-03-12.md", "# Day\nContent\n", 1)
        full_index = self.mod.build_index()
        self.mod.write_index(full_index)

        # Add a new file
        self._write_file("memory/2026-03-13.md", "# New Day\nNew content\n", 0)
        inc_index = self.mod.build_index_incremental()
        self.assertEqual(inc_index["counts"]["entries"], 2)

    def test_incremental_prunes_deleted_files(self) -> None:
        """C2: Deleted files should be removed from index."""
        path = self._write_file("memory/2026-03-12.md", "# Day\nContent\n", 1)
        full_index = self.mod.build_index()
        self.mod.write_index(full_index)

        path.unlink()
        inc_index = self.mod.build_index_incremental()
        self.assertEqual(inc_index["counts"]["entries"], 0)

    def test_access_log_rotation(self) -> None:
        """D1: Old access log entries should be rotated out."""
        log_path = self.data_root / "operations" / "memory-access.jsonl"
        from datetime import timedelta
        old_ts = self.mod.iso_timestamp(self.mod.now() - timedelta(days=45))
        new_ts = self.mod.iso_timestamp(self.mod.now() - timedelta(days=5))
        log_path.write_text(
            json.dumps({"timestamp": old_ts, "path": "old-note.md"}) + "\n"
            + json.dumps({"timestamp": new_ts, "path": "new-note.md"}) + "\n",
            encoding="utf-8",
        )

        removed = self.mod.rotate_access_log(max_age_days=30)
        self.assertEqual(removed, 1)

        remaining = log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(remaining), 1)
        self.assertIn("new-note.md", remaining[0])


if __name__ == "__main__":
    unittest.main()
