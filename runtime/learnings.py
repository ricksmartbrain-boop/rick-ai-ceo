#!/usr/bin/env python3
"""Parse and apply self-growth learnings for Rick v6."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LEARNINGS_DIR = DATA_ROOT / "reflections" / "learnings"
LATEST_FILE = LEARNINGS_DIR / "latest.json"


def load_latest_learnings() -> dict:
    """Read the most recent self-growth learnings output."""
    if not LATEST_FILE.exists():
        return {}
    try:
        return json.loads(LATEST_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def corrective_actions() -> list[dict]:
    """Extract actionable corrective actions from learnings."""
    learnings = load_latest_learnings()
    actions = learnings.get("corrective_actions", [])
    if isinstance(actions, list):
        return actions
    return []


def queued_initiatives() -> list[dict]:
    """Extract queued initiatives from learnings."""
    learnings = load_latest_learnings()
    initiatives = learnings.get("queued_initiatives", [])
    if isinstance(initiatives, list):
        return initiatives
    return []


def repeated_failures() -> list[str]:
    """Extract repeated failure patterns."""
    learnings = load_latest_learnings()
    patterns = learnings.get("repeated_failure_patterns", [])
    if isinstance(patterns, list):
        return [str(p) for p in patterns]
    return []


def wins() -> list[str]:
    """Extract recent wins."""
    learnings = load_latest_learnings()
    win_list = learnings.get("wins", [])
    if isinstance(win_list, list):
        return [str(w) for w in win_list]
    return []


def learnings_summary() -> dict:
    """Compact summary suitable for context packs."""
    learnings = load_latest_learnings()
    if not learnings:
        return {"available": False}
    return {
        "available": True,
        "generated_at": learnings.get("generated_at", ""),
        "failure_count": len(learnings.get("repeated_failure_patterns", [])),
        "win_count": len(learnings.get("wins", [])),
        "corrective_count": len(learnings.get("corrective_actions", [])),
        "initiative_count": len(learnings.get("queued_initiatives", [])),
        "top_failures": learnings.get("repeated_failure_patterns", [])[:3],
        "top_wins": learnings.get("wins", [])[:3],
    }


_AUTO_APPLY_LOG = DATA_ROOT / "control" / "auto-applied-corrective-actions.jsonl"
_PENDING_REVIEW = DATA_ROOT / "control" / "pending-corrective-actions.md"
_BUDGETS_FILE_PATH = Path(os.path.expanduser(
    os.getenv("RICK_WORKFLOW_BUDGETS_FILE",
              str(Path(__file__).resolve().parent.parent / "config" / "workflow-budgets.json"))
))


def _action_hash(action: dict) -> str:
    """Stable hash for idempotency. Same action text + priority = same hash."""
    import hashlib
    text = (action.get("action", "") + "|" + action.get("priority", "")).strip().lower()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _already_applied_this_week(action_hash: str) -> bool:
    if not _AUTO_APPLY_LOG.exists():
        return False
    cutoff = datetime.now().timestamp() - (7 * 86400)
    try:
        for line in _AUTO_APPLY_LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("hash") == action_hash:
                ts = datetime.fromisoformat(row.get("at", "")).timestamp()
                if ts > cutoff:
                    return True
    except (json.JSONDecodeError, OSError, ValueError):
        return False
    return False


def _record_auto_applied(action: dict, mutation: str, action_hash: str) -> None:
    _AUTO_APPLY_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "hash": action_hash,
        "action": action.get("action", ""),
        "priority": action.get("priority", ""),
        "mutation": mutation,
    }
    with _AUTO_APPLY_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _try_disable_workflow_kind(action: dict) -> str | None:
    """Allowlist mutation: 'disable workflow kind X' → set its budget cap to 0.

    Pattern matches: 'disable <kind>', 'stop <kind> workflow', 'kill <kind>'.
    Only applies when action mentions a kind we know about + priority=high.
    Returns mutation description on success, None if pattern doesn't apply.
    """
    if action.get("priority", "").lower() != "high":
        return None
    text = (action.get("action", "") + " " + action.get("rationale", "")).lower()
    if not any(verb in text for verb in ["disable", "kill workflow", "stop workflow", "halt workflow"]):
        return None
    if not _BUDGETS_FILE_PATH.exists():
        return None
    try:
        budgets = json.loads(_BUDGETS_FILE_PATH.read_text(encoding="utf-8"))
        caps = budgets.get("caps_usd_per_day", {})
        # Find first known kind mentioned in the action text
        target_kind = None
        for kind in caps.keys():
            if kind.startswith("_"):
                continue
            if kind.replace("_", " ") in text or kind in text:
                target_kind = kind
                break
        if not target_kind:
            return None
        if caps.get(target_kind, 5.0) == 0.0:
            return None  # Already disabled, no-op
        caps[target_kind] = 0.0
        budgets["caps_usd_per_day"] = caps
        _BUDGETS_FILE_PATH.write_text(json.dumps(budgets, indent=2), encoding="utf-8")
        return f"workflow-budgets.json: set caps_usd_per_day[{target_kind}] = 0.00"
    except (json.JSONDecodeError, OSError):
        return None


def _write_pending_review(non_applied: list[dict]) -> None:
    if not non_applied:
        return
    _PENDING_REVIEW.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "type: pending-corrective-actions",
        f"generated: {datetime.now().isoformat(timespec='seconds')}",
        f"count: {len(non_applied)}",
        "tags: [learnings, corrective, needs-vlad]",
        "---",
        "",
        "# Corrective Actions Awaiting Vlad's Review",
        "",
        "These were extracted by the daily learning cycle but did NOT match the",
        "auto-apply allowlist (disable_workflow_kind). They need manual decision:",
        "implement them as code edits, or dismiss as not-actionable.",
        "",
    ]
    for i, action in enumerate(non_applied, 1):
        lines.append(f"## {i}. {action.get('action', 'Unknown action')}")
        if action.get("rationale"):
            lines.append(f"**Why:** {action['rationale']}")
        if action.get("priority"):
            lines.append(f"**Priority:** {action['priority']}")
        lines.append("")
    _PENDING_REVIEW.write_text("\n".join(lines), encoding="utf-8")


def apply_corrective_actions() -> list[str]:
    """TIER-1 #2 (2026-04-23) — close the self-learning loop.

    For each corrective action surfaced by the daily learning cycle:
      1. Idempotency: skip if same action_hash applied within last 7 days.
      2. Allowlist match: try _try_disable_workflow_kind (the only safe
         mutation today). Future allowlist additions go here.
      3. Auto-apply if matched + priority=high. Log to
         control/auto-applied-corrective-actions.jsonl.
      4. Otherwise: collect into pending-review pile.

    Returns a flat list of `[timestamp] description` strings (the prior
    log-only contract), so existing callers don't break.

    Disable: RICK_CORRECTIVE_AUTO_APPLY_DISABLED=1.
    """
    auto_apply_disabled = os.getenv("RICK_CORRECTIVE_AUTO_APPLY_DISABLED", "").strip().lower() in ("1", "true", "yes")
    actions = corrective_actions()
    applied_log: list[str] = []
    pending: list[dict] = []
    for action in actions:
        if not isinstance(action, dict):
            applied_log.append(f"[{datetime.now().isoformat(timespec='seconds')}] Logged (non-dict): {action}")
            continue
        desc = action.get("action", action.get("description", str(action)))
        h = _action_hash(action)
        if _already_applied_this_week(h):
            applied_log.append(f"[{datetime.now().isoformat(timespec='seconds')}] SKIP (applied within 7d): {desc}")
            continue
        if auto_apply_disabled:
            pending.append(action)
            continue
        mutation = _try_disable_workflow_kind(action)
        if mutation:
            _record_auto_applied(action, mutation, h)
            applied_log.append(f"[{datetime.now().isoformat(timespec='seconds')}] AUTO-APPLIED: {desc} → {mutation}")
        else:
            pending.append(action)
    _write_pending_review(pending)
    if pending:
        applied_log.append(
            f"[{datetime.now().isoformat(timespec='seconds')}] WROTE pending-review pile ({len(pending)} actions) → {_PENDING_REVIEW}"
        )
    return applied_log


def write_corrective_actions_to_vault() -> None:
    """Write pending corrective actions to vault for visibility and tracking."""
    actions = corrective_actions()
    if not actions:
        return
    output_path = LEARNINGS_DIR / "corrective-actions.md"
    lines = [
        "---",
        "type: corrective-actions",
        f"generated: {datetime.now().isoformat(timespec='seconds')}",
        f"count: {len(actions)}",
        "tags: [learnings, corrective, automation]",
        "---",
        "",
        "# Pending Corrective Actions",
        "",
    ]
    for i, action in enumerate(actions, 1):
        if isinstance(action, dict):
            lines.append(f"## {i}. {action.get('action', 'Unknown')}")
            if action.get('rationale'):
                lines.append(f"**Why:** {action['rationale']}")
            if action.get('priority'):
                lines.append(f"**Priority:** {action['priority']}")
            lines.append("")
        else:
            lines.append(f"## {i}. {action}")
            lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def ensure_learnings_dir() -> None:
    """Create the learnings directory if it doesn't exist."""
    LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)
