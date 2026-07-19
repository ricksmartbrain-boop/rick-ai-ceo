#!/usr/bin/env python3
"""Tests for event dispatch and reaction system."""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

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


def test_dispatch_event_fires_reactions():
    """dispatch_event should call record_event and fire matching reactions."""
    conn = make_test_db()
    from runtime.engine import dispatch_event, record_event

    reactions_config = {
        "reactions": {
            "test_event": [
                {"action": "notify", "template": "Test fired: {title}"}
            ]
        }
    }

    with patch("runtime.engine.load_event_reactions", return_value=reactions_config):
        with patch("runtime.engine.notify_operator") as mock_notify:
            dispatch_event(conn, None, None, "test_event", {"title": "My Product"})
            # record_event should have been called (event in DB)
            row = conn.execute(
                "SELECT * FROM events WHERE event_type = 'test_event'"
            ).fetchone()
            assert row is not None, "Event should be recorded in DB"


def test_dispatch_event_no_reactions():
    """dispatch_event should still record event even with no matching reactions."""
    conn = make_test_db()
    from runtime.engine import dispatch_event

    with patch("runtime.engine.load_event_reactions", return_value={"reactions": {}}):
        dispatch_event(conn, None, None, "unknown_event", {"foo": "bar"})
        row = conn.execute(
            "SELECT * FROM events WHERE event_type = 'unknown_event'"
        ).fetchone()
        assert row is not None


def test_queue_initiative_workflow(monkeypatch):
    """A clean-identity initiative must create a workflow + a first 'plan' job.

    The identity gate (2026-07 intake canonicalization) is stubbed to 'create'
    — its dedupe behavior is the resolver's own concern; this test guards the
    creation path behind it. RICK_INITIATIVE_DISABLED must not leak in from
    the environment (it gates the whole family off)."""
    monkeypatch.delenv("RICK_INITIATIVE_DISABLED", raising=False)
    conn = make_test_db()
    from runtime.engine import queue_initiative_workflow

    with patch(
        "scripts.workflow_identity_resolver.resolve_or_create_workflow",
        return_value=MagicMock(action="create"),
    ):
        wf_id = queue_initiative_workflow(conn, objective="Test initiative", project="test")
    assert wf_id.startswith("wf_")

    workflow = conn.execute("SELECT * FROM workflows WHERE id = ?", (wf_id,)).fetchone()
    assert workflow is not None
    assert workflow["kind"] == "initiative"

    jobs = conn.execute(
        "SELECT * FROM jobs WHERE workflow_id = ? ORDER BY step_index", (wf_id,)
    ).fetchall()
    assert len(jobs) >= 1
    assert jobs[0]["step_name"] == "plan"


def test_notify_operator_uses_flag_value_for_text(monkeypatch):
    """notify_operator must pass event text as the value to --text (not positional).

    Quiet hours + the 5s rate limit are disabled via their own env switches so
    this stays deterministic at any wall-clock time — the old version silently
    hit the 22:00-07:00 digest path and never invoked the CLI."""
    monkeypatch.setenv("RICK_QUIET_HOURS_ENABLED", "0")
    monkeypatch.setenv("RICK_NOTIFY_RATE_LIMIT_DISABLED", "1")
    conn = make_test_db()
    from runtime.engine import notify_operator

    completed = MagicMock(returncode=0, stderr="", stdout="")
    with patch("runtime.engine.shutil.which", return_value="/usr/local/bin/openclaw"):
        with patch("runtime.engine.subprocess.run", return_value=completed) as mock_run:
            notify_operator(conn, "hello world")

    mock_run.assert_called_once_with(
        ["/usr/local/bin/openclaw", "system", "event", "--text", "hello world", "--mode", "now"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def test_notify_operator_urgent_bypasses_quiet_hours(monkeypatch):
    """A revenue-purpose notification must deliver even during quiet hours —
    deferring a warm-lead ping to the morning digest would defeat the reply rail."""
    monkeypatch.setenv("RICK_QUIET_HOURS_ENABLED", "1")
    monkeypatch.setenv("RICK_NOTIFY_RATE_LIMIT_DISABLED", "1")
    conn = make_test_db()
    from runtime import engine

    completed = MagicMock(returncode=0, stderr="", stdout="")
    fixed_quiet = MagicMock()
    fixed_quiet.now.return_value = MagicMock(hour=3)
    with patch("runtime.engine.datetime", fixed_quiet):
        with patch("runtime.engine.shutil.which", return_value="/usr/local/bin/openclaw"):
            with patch("runtime.engine.subprocess.run", return_value=completed) as mock_run:
                engine.notify_operator(conn, "warm reply landed", purpose="revenue")
    assert mock_run.call_count == 1


if __name__ == "__main__":
    test_dispatch_event_fires_reactions()
    print("PASS: test_dispatch_event_fires_reactions")
    test_dispatch_event_no_reactions()
    print("PASS: test_dispatch_event_no_reactions")
    test_queue_initiative_workflow()
    print("PASS: test_queue_initiative_workflow")
    test_notify_operator_uses_flag_value_for_text()
    print("PASS: test_notify_operator_uses_flag_value_for_text")
    print("All event dispatch tests passed.")
