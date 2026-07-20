#!/usr/bin/env python3
"""Tests for graduated overnight confidence tiers."""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from runtime.db import init_db


def make_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def insert_workflow(conn: sqlite3.Connection, wf_id: str) -> None:
    # Schema enforces outcomes.workflow_id -> workflows(id): every outcome must
    # trace back to a real workflow, so fixtures create the parent row first.
    conn.execute(
        """INSERT INTO workflows (id, kind, title, slug, project, status, stage,
                                  context_json, created_at, updated_at)
           VALUES (?, 'test', ?, ?, 'test-proj', 'active', 'execute',
                   '{}', datetime('now'), datetime('now'))""",
        (wf_id, f"Test {wf_id}", wf_id),
    )


def test_high_confidence_tier():
    """0 failures + 3+ wins = high tier."""
    conn = make_test_db()
    from runtime.engine import overnight_confidence_tier

    # Insert 3 successful outcomes, 0 failures
    for i in range(3):
        insert_workflow(conn, f"wf_{i}")
        conn.execute(
            """INSERT INTO outcomes (workflow_id, step_name, route, outcome_type, created_at)
               VALUES (?, ?, ?, 'success', datetime('now', ?))""",
            (f"wf_{i}", f"step_{i}", "writing", f"-{i} hours"),
        )
    conn.commit()

    tier = overnight_confidence_tier(conn)
    assert tier == "high", f"Expected high, got {tier}"


def test_medium_confidence_tier():
    """1 failure = medium tier."""
    conn = make_test_db()
    from runtime.engine import overnight_confidence_tier

    for i in range(3):
        insert_workflow(conn, f"wf_{i}")
        conn.execute(
            """INSERT INTO outcomes (workflow_id, step_name, route, outcome_type, created_at)
               VALUES (?, ?, ?, 'success', datetime('now', ?))""",
            (f"wf_{i}", f"step_{i}", "writing", f"-{i} hours"),
        )
    insert_workflow(conn, "wf_fail")
    conn.execute(
        """INSERT INTO outcomes (workflow_id, step_name, route, outcome_type, created_at)
           VALUES ('wf_fail', 'step_fail', 'writing', 'failure', datetime('now'))"""
    )
    conn.commit()

    tier = overnight_confidence_tier(conn)
    assert tier == "medium", f"Expected medium, got {tier}"


def test_low_confidence_tier():
    """2+ failures = low tier."""
    conn = make_test_db()
    from runtime.engine import overnight_confidence_tier

    for i in range(3):
        insert_workflow(conn, f"wf_{i}")
        conn.execute(
            """INSERT INTO outcomes (workflow_id, step_name, route, outcome_type, created_at)
               VALUES (?, ?, ?, 'failure', datetime('now', ?))""",
            (f"wf_{i}", f"step_{i}", "writing", f"-{i} hours"),
        )
    conn.commit()

    tier = overnight_confidence_tier(conn)
    assert tier == "low", f"Expected low, got {tier}"


def test_tiered_auto_approval_high():
    """High tier should auto-approve reversible-content."""
    conn = make_test_db()
    from runtime.engine import CONFIDENCE_TIERS

    tier_config = CONFIDENCE_TIERS["high"]
    assert "reversible-content" in tier_config["auto_approve"]
    assert "irreversible-brand" not in tier_config["auto_approve"]


def test_tiered_auto_approval_low():
    """Low tier should not auto-approve anything."""
    conn = make_test_db()
    from runtime.engine import CONFIDENCE_TIERS

    tier_config = CONFIDENCE_TIERS["low"]
    assert len(tier_config["auto_approve"]) == 0


if __name__ == "__main__":
    test_high_confidence_tier()
    print("PASS: test_high_confidence_tier")
    test_medium_confidence_tier()
    print("PASS: test_medium_confidence_tier")
    test_low_confidence_tier()
    print("PASS: test_low_confidence_tier")
    test_tiered_auto_approval_high()
    print("PASS: test_tiered_auto_approval_high")
    test_tiered_auto_approval_low()
    print("PASS: test_tiered_auto_approval_low")
    print("All overnight tier tests passed.")
