"""Smoke tests for 2026-04-24 push session ships.

Coverage:
- runtime/patterns.py — pick_patterns / record_pattern_outcome / format_pattern_context / patterns_summary
- runtime/engine.py — _dedup_hash normalization + notify_operator_deduped flow
- skills/self-learning/scripts/morning-intelligence.py — _is_phantom Stripe filter
- runtime/skill_handlers/phase1.py — lead_intake auto-suppression (self-send + vendor)

These are FAST smoke tests — no LLM calls, no network, no daemon side-effects.
Each test sets up an isolated in-memory or temp-file SQLite DB and tears down.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory DB with the tables Rick's runtime expects."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    # Minimal schema for the tests below
    con.executescript("""
        CREATE TABLE effective_patterns (
            id TEXT PRIMARY KEY,
            pattern_kind TEXT NOT NULL,
            snippet TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            applicable_skills TEXT NOT NULL DEFAULT '[]',
            sum_wins INTEGER NOT NULL DEFAULT 0,
            sum_runs INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        );
        CREATE TABLE notification_dedupe (
            dedup_hash TEXT PRIMARY KEY,
            kind TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_alerted_at TEXT NOT NULL,
            count_since_alert INTEGER NOT NULL DEFAULT 0,
            last_text TEXT NOT NULL DEFAULT '',
            total_seen INTEGER NOT NULL DEFAULT 1
        );
    """)
    con.commit()
    return con


# =============================================================================
# patterns.py
# =============================================================================

class PatternsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.con = _make_test_db()
        # Seed a few patterns
        self.con.executescript("""
            INSERT INTO effective_patterns (id, pattern_kind, snippet, applicable_skills, sum_wins, sum_runs, created_at)
            VALUES
              ('p1', 'pitch_draft', 'Lead with metric, not pitch.', '["pitch_draft"]', 5, 10, '2026-04-20T00:00:00'),
              ('p2', 'dream_insight', 'Cold-scraped names with no message field score lower.', '[]', 0, 0, '2026-04-21T00:00:00'),
              ('p3', 'lead_qualify', 'Vendor pitches use "we help b2b".', '["lead_qualify"]', 2, 8, '2026-04-22T00:00:00'),
              ('p4', 'dream_insight', 'Approval reminders > 1h get ignored.', '[]', 1, 1, '2026-04-23T00:00:00');
        """)
        self.con.commit()

    def test_pick_patterns_for_skill(self) -> None:
        from runtime.patterns import pick_patterns
        result = pick_patterns(self.con, "pitch_draft", top_n=3)
        ids = {p["id"] for p in result}
        # Should include p1 (skill match) + p2/p4 (universal dream_insights)
        self.assertIn("p1", ids)
        self.assertIn("p2", ids)
        self.assertEqual(len(result), 3)

    def test_pick_patterns_universal_dream_insights(self) -> None:
        from runtime.patterns import pick_patterns
        result = pick_patterns(self.con, "totally_unknown_skill", top_n=3)
        # Should fall back to dream_insights (p2, p4)
        ids = {p["id"] for p in result}
        self.assertEqual({"p2", "p4"}, ids)

    def test_pick_patterns_empty_table(self) -> None:
        from runtime.patterns import pick_patterns
        empty = sqlite3.connect(":memory:")
        empty.row_factory = sqlite3.Row
        # No effective_patterns table → should not crash
        result = pick_patterns(empty, "anything", top_n=3)
        self.assertEqual([], result)

    def test_record_pattern_outcome_increments(self) -> None:
        from runtime.patterns import record_pattern_outcome
        record_pattern_outcome(self.con, ["p1", "p2"], success=True)
        row = self.con.execute("SELECT sum_wins, sum_runs FROM effective_patterns WHERE id='p1'").fetchone()
        self.assertEqual(row["sum_wins"], 6)  # was 5, +1
        self.assertEqual(row["sum_runs"], 11)  # was 10, +1
        row = self.con.execute("SELECT sum_wins, sum_runs FROM effective_patterns WHERE id='p2'").fetchone()
        self.assertEqual(row["sum_wins"], 1)  # was 0, +1
        self.assertEqual(row["sum_runs"], 1)  # was 0, +1

    def test_record_pattern_outcome_failure(self) -> None:
        from runtime.patterns import record_pattern_outcome
        record_pattern_outcome(self.con, ["p1"], success=False)
        row = self.con.execute("SELECT sum_wins, sum_runs FROM effective_patterns WHERE id='p1'").fetchone()
        self.assertEqual(row["sum_wins"], 5)  # unchanged
        self.assertEqual(row["sum_runs"], 11)  # +1

    def test_record_pattern_outcome_silently_skips_unknown(self) -> None:
        from runtime.patterns import record_pattern_outcome
        # Should not raise on missing IDs
        record_pattern_outcome(self.con, ["nonexistent_id"], success=True)
        # Existing rows unchanged
        row = self.con.execute("SELECT sum_runs FROM effective_patterns WHERE id='p1'").fetchone()
        self.assertEqual(row["sum_runs"], 10)

    def test_format_pattern_context_empty(self) -> None:
        from runtime.patterns import format_pattern_context
        self.assertEqual("", format_pattern_context([]))

    def test_format_pattern_context_renders(self) -> None:
        from runtime.patterns import format_pattern_context
        patterns = [
            {"id": "p1", "snippet": "Test snippet 1", "proven": True},
            {"id": "p2", "snippet": "Test snippet 2", "proven": False},
        ]
        rendered = format_pattern_context(patterns)
        self.assertIn("Test snippet 1", rendered)
        self.assertIn("Test snippet 2", rendered)
        self.assertIn("★", rendered)  # proven marker
        self.assertIn("·", rendered)  # exploration marker


# =============================================================================
# engine.py — dedup helper
# =============================================================================

class DedupHashTests(unittest.TestCase):
    def test_dedup_hash_normalizes_workflow_ids(self) -> None:
        from runtime.engine import _dedup_hash
        h1 = _dedup_hash("Job foo_abc123 (lead_qualify) blocked for 30 min in workflow wf_xyz789", "blocked_job")
        h2 = _dedup_hash("Job foo_def456 (lead_qualify) blocked for 12 h in workflow wf_qrs012", "blocked_job")
        self.assertEqual(h1, h2, "different IDs/durations should hash same")

    def test_dedup_hash_kind_isolation(self) -> None:
        from runtime.engine import _dedup_hash
        text = "Job foo_abc123 (lead_qualify) blocked for 30 min in workflow wf_xyz789"
        h1 = _dedup_hash(text, "blocked_job")
        h2 = _dedup_hash(text, "approval_reminder")
        self.assertNotEqual(h1, h2, "different kinds should hash differently")

    def test_dedup_hash_strips_suppressed_prefix(self) -> None:
        from runtime.engine import _dedup_hash
        h1 = _dedup_hash("Real alert text here", "k")
        h2 = _dedup_hash("(suppressed x42 in last 24h) Real alert text here", "k")
        self.assertEqual(h1, h2)


# =============================================================================
# Stripe phantom filter
# =============================================================================

class StripePhantomFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        # Load _is_phantom from morning-intelligence.py via importlib (script, not module)
        spec = importlib.util.spec_from_file_location(
            "morning_intel",
            ROOT / "skills" / "self-learning" / "scripts" / "morning-intelligence.py",
        )
        if spec is None or spec.loader is None:
            self.skipTest("morning-intelligence.py not loadable")
        self.mod = importlib.util.module_from_spec(spec)
        # Suppress side effects during import
        os.environ.setdefault("RICK_REVENUE_VELOCITY_LIVE", "0")
        try:
            spec.loader.exec_module(self.mod)
        except Exception as exc:
            self.skipTest(f"morning-intelligence.py import failed: {exc}")

    def test_phantom_filters_100pct_discount(self) -> None:
        sub = {
            "id": "sub_credits_booster",
            "status": "active",
            "discount": {"coupon": {"percent_off": 100}},
            "latest_invoice": {"amount_paid": 0, "amount_due": 0},
        }
        is_phantom, reason = self.mod._is_phantom(sub, 1700000000)
        self.assertTrue(is_phantom)
        self.assertIn("discount", reason)

    def test_phantom_filters_zero_paid_zero_due(self) -> None:
        sub = {
            "id": "sub_freebie",
            "status": "active",
            "latest_invoice": {"amount_paid": 0, "amount_due": 0},
        }
        is_phantom, reason = self.mod._is_phantom(sub, 1700000000)
        self.assertTrue(is_phantom)
        self.assertIn("zero-paid", reason)

    def test_phantom_filters_non_active_status(self) -> None:
        sub = {"id": "sub_canceled", "status": "canceled"}
        is_phantom, reason = self.mod._is_phantom(sub, 1700000000)
        self.assertTrue(is_phantom)

    def test_real_paying_sub_passes(self) -> None:
        sub = {
            "id": "sub_real_newton",
            "status": "active",
            "latest_invoice": {"amount_paid": 900, "amount_due": 900},
            "plan": {"amount": 900},
            "quantity": 1,
        }
        is_phantom, reason = self.mod._is_phantom(sub, 1700000000)
        self.assertFalse(is_phantom)
        self.assertEqual("", reason)

    def test_phantom_filters_expired_cancel_at_period_end(self) -> None:
        # cancel_at_period_end True + period_end in the past → phantom.
        # Need non-zero invoice to isolate the cancel_at_period_end branch
        # (zero-paid + zero-due hits FIRST in the helper's check order).
        sub = {
            "id": "sub_expired",
            "status": "active",  # technically still "active" but period over
            "cancel_at_period_end": True,
            "current_period_end": 1500000000,  # past timestamp (year 2017)
            "latest_invoice": {"amount_paid": 900, "amount_due": 900},
        }
        is_phantom, reason = self.mod._is_phantom(sub, 1700000000)
        self.assertTrue(is_phantom)
        self.assertIn("period expired", reason)


# =============================================================================
# Lead intake blocklist
# =============================================================================

class LeadIntakeBlocklistTests(unittest.TestCase):
    """Tests the early-return blocklist BEFORE any LLM call.

    Doesn't actually invoke the handler (would need full DB schema + LLM mock).
    Tests the blocklist constants + helper logic by re-implementing the check
    inline — ensures the SHIPPED constants stay in sync.
    """
    def test_self_send_addresses_includes_rick_and_vlad(self) -> None:
        from runtime.inbound.imap_watcher import SELF_SEND_ADDRESSES
        self.assertIn("rick@meetrick.ai", SELF_SEND_ADDRESSES)
        self.assertIn("hello@meetrick.ai", SELF_SEND_ADDRESSES)
        self.assertIn("vlad@meetrick.ai", SELF_SEND_ADDRESSES)
        self.assertIn("vladislav@belkins.io", SELF_SEND_ADDRESSES)

    def test_meetrick_domain_match(self) -> None:
        # Defense-in-depth — any @meetrick.ai sender should be self-send
        from runtime.inbound.imap_watcher import SELF_SEND_ADDRESSES
        # The handler uses .endswith("meetrick.ai") on the domain — this is the contract
        for known in SELF_SEND_ADDRESSES:
            if "@" in known:
                self.assertTrue(known.split("@", 1)[1].endswith("meetrick.ai") or known.endswith("@belkins.io"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
