#!/usr/bin/env python3
"""Shared fail-closed email safety helpers for legacy sender scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"


def email_channel_block_reason() -> str | None:
    """Return a reason when the runtime email channel is not active."""
    try:
        root = str(WORKSPACE_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from runtime.db import connect
        from runtime.kill_switches import ChannelPaused, assert_channel_active

        conn = connect()
        try:
            assert_channel_active(conn, "email")
            return None
        except ChannelPaused as exc:
            return exc.reason
        finally:
            conn.close()
    except Exception as exc:
        return f"gate_unavailable: {type(exc).__name__}: {exc}"


def is_suppressed(email: str) -> bool:
    """Return True when the local bounce/unsubscribe list blocks the recipient."""
    target = (email or "").strip().lower()
    if not target:
        return True
    if not SUPPRESSION_FILE.exists():
        return False
    try:
        lines = SUPPRESSION_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return True
    for raw in lines:
        suppressed = raw.split("#", 1)[0].strip().lower()
        if suppressed and suppressed == target:
            return True
    return False


def block_reason_for_recipient(email: str) -> str | None:
    """Return a fail-closed block reason for a recipient, or None when safe."""
    channel_reason = email_channel_block_reason()
    if channel_reason:
        return f"channel_paused: {channel_reason}"
    # Unified per-recipient gate (2026-07-13): master kill + RICK_EMAIL_SEND_LIVE
    # + merged suppression/DNC (domain-aware) + 7-day cold frequency cap. The
    # scripts using this helper are cold first-touch blasts → cold=True.
    try:
        root = str(WORKSPACE_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from runtime.kill_switches import is_send_allowed

        allowed, gate_reason = is_send_allowed(email, cold=True)
        if not allowed:
            return f"SEND_BLOCKED reason={gate_reason} to={(email or '').strip().lower()}"
    except Exception as exc:
        return f"gate_unavailable: {type(exc).__name__}: {exc}"
    if is_suppressed(email):
        return f"suppressed: {(email or '').strip().lower()}"
    return None
