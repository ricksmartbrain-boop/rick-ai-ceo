#!/usr/bin/env python3
"""tg_notify.py — Canonical Telegram topic router for Rick.

Replaces direct tg-topic.sh subprocess calls with 'openclaw message send'.
This module is the single source of truth for the topic → (chat_id, thread_id) map
(migrated from tg-topic.sh, Strategy-C #1, 2026-05-04).

Usage as module:
    from scripts.tg_notify import send as tg_send
    tg_send("ops-alerts", "🚨 something broke")

Usage as CLI:
    python3 scripts/tg_notify.py ops-alerts "your message"
    python3 scripts/tg_notify.py --dry-run customer "test"
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ── Topic → (chat_id, thread_id) map ─────────────────────────────────────────
# Migrated verbatim from tg-topic.sh (2026-05-04).
# Chat IDs sourced from env vars with hardcoded defaults.

def _env(key: str, default: str) -> str:
    return (os.environ.get(key) or "").strip() or default


def _build_map() -> dict[str, tuple[str, int]]:
    team = _env("RICK_TEAM_CHAT_ID", "-1003781085932")
    wr   = _env("RICK_WAR_ROOM_CHAT_ID", "-1003817549117")
    return {
        # ── Vlad & Rick Team ──────────────────────────────────────────────
        "ceo-hq":       (team, 24),
        "ceo":          (team, 24),
        "approvals":    (team, 26),
        "product-lab":  (team, 28),
        "product":      (team, 28),
        "distribution": (team, 30),
        "dist":         (team, 30),
        "customer":     (team, 32),
        "ops-alerts":   (team, 34),
        "ops":          (team, 34),
        "test":         (team, 36),
        "traffic":      (team, 715),
        "analytics":    (team, 715),
        # ── War Room ─────────────────────────────────────────────────────
        "ideas":        (wr, 4),
        "hot-takes":    (wr, 5),
        "wr-product":   (wr, 6),
        "war-room":     (wr, 7),
        "wr":           (wr, 7),
        "intros":       (wr, 8),
        "rick-output":  (wr, 9),
        "output":       (wr, 9),
    }


# Build once at import time so env-var overrides in rick.env are picked up.
TOPIC_MAP: dict[str, tuple[str, int]] = _build_map()

_WORKSPACE   = Path(__file__).resolve().parent.parent
_TG_FALLBACK = _WORKSPACE / "scripts" / "tg-topic.sh"


def send(
    topic: str,
    text: str,
    *,
    fallback: bool = True,
    timeout: int = 20,
    dry_run: bool = False,
) -> bool:
    """Send *text* to the named Telegram *topic*.

    Primary path:  openclaw message send --channel telegram --target <chat> --thread-id <tid>
    Fallback path: bash tg-topic.sh <topic> <text>   (only when fallback=True)

    Returns True on success, False when all paths fail.
    """
    mapping = TOPIC_MAP.get(topic)
    if not mapping:
        _warn(f"unknown topic: {topic!r}  (known: {', '.join(sorted(TOPIC_MAP))})")
        return False

    chat_id, thread_id = mapping

    if dry_run:
        print(f"[tg_notify] DRY-RUN  topic={topic!r}  chat={chat_id}  tid={thread_id}")
        print(f"[tg_notify] message: {text[:120]}")
        return True

    # ── Primary: openclaw message send ───────────────────────────────────────
    try:
        result = subprocess.run(
            [
                "openclaw", "message", "send",
                "--channel", "telegram",
                "--target", chat_id,
                "--thread-id", str(thread_id),
                "--message", text,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return True
        _warn(
            f"openclaw message send failed (rc={result.returncode}): "
            f"{(result.stderr or result.stdout or '')[:200]}"
        )
    except subprocess.TimeoutExpired:
        _warn("openclaw message send timed out")
    except OSError as exc:
        _warn(f"openclaw message send OS error: {exc}")

    if not fallback:
        return False

    # ── Fallback: tg-topic.sh ────────────────────────────────────────────────
    if _TG_FALLBACK.is_file():
        try:
            r = subprocess.run(
                ["bash", str(_TG_FALLBACK), topic, text],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if r.returncode == 0:
                return True
            _warn(f"tg-topic.sh fallback failed (rc={r.returncode}): {r.stderr[:200]}")
        except Exception as exc2:
            _warn(f"tg-topic.sh fallback exception: {exc2}")
    else:
        _warn("tg-topic.sh fallback not found — all send paths failed")

    return False


def _warn(msg: str) -> None:
    print(f"[tg_notify] {msg}", file=sys.stderr)


# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Send a message to a named Telegram topic.")
    ap.add_argument("topic", help=f"Topic name. Options: {', '.join(sorted(TOPIC_MAP))}")
    ap.add_argument("message", help="Message text")
    ap.add_argument("--dry-run", action="store_true", help="Print payload without sending")
    ap.add_argument("--no-fallback", action="store_true", help="Disable tg-topic.sh fallback")
    args = ap.parse_args()

    ok = send(args.topic, args.message, fallback=not args.no_fallback, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
