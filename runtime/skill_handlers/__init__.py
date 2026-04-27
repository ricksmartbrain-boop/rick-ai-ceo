"""Skill handler registry for Rick v6 — 15 revenue skills across 4 phases."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3
    from runtime.engine import StepOutcome

_HANDLERS: dict | None = None


def get_all_handlers() -> dict:
    """Lazy-load all skill handlers to avoid circular imports with engine.py."""
    global _HANDLERS
    if _HANDLERS is not None:
        return _HANDLERS

    from runtime.skill_handlers.phase1 import PHASE1_HANDLERS
    from runtime.skill_handlers.phase2 import PHASE2_HANDLERS
    from runtime.skill_handlers.phase3 import PHASE3_HANDLERS
    from runtime.skill_handlers.phase4 import PHASE4_HANDLERS

    _HANDLERS = {}
    _HANDLERS.update(PHASE1_HANDLERS)
    _HANDLERS.update(PHASE2_HANDLERS)
    _HANDLERS.update(PHASE3_HANDLERS)
    _HANDLERS.update(PHASE4_HANDLERS)
    return _HANDLERS
