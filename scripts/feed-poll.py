#!/usr/bin/env python3
"""feed-poll.py — hourly orchestrator for RSS feed ingestion (TIER-3.6).

Polls meetrick-rss-ingest at api.meetrick.ai for new feed items and
dispatches by score and source:
  • shir-man (algo-scored):
      sm_algo_score > 0.9   → queue HIGH-PRIORITY `feed_riff` workflow
      0.75 <= score <= 0.9  → append to ~/rick-vault/state/roundup-queue-YYYY-MM-DD.jsonl
  • belkins / folderly / vlad-newsletter:
      ALWAYS queue `proof_repurpose` workflow

State:
  ~/rick-vault/state/feed-last-seen.txt   — last successfully processed id

Logs:
  ~/rick-vault/operations/feed-poll.jsonl

Safety:
  - DRY-RUN by default. Set RICK_FEED_POLL_LIVE=1 to actually queue work.
  - Gracefully no-ops if MEETRICK_API_BASE returns 404/503 (service not
    deployed yet — this is the expected state until Vlad runs `railway up`
    on the meetrick-rss-ingest service).
  - Never crashes — every section wrapped in try/except.

CLI:
  --dry-run       force dry-run even if RICK_FEED_POLL_LIVE=1
  --max-items=50  cap items processed this run (default 50)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# Make `runtime` importable when called from cron / launchd.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
STATE_DIR = DATA_ROOT / "state"
OPS_DIR = DATA_ROOT / "operations"
LAST_SEEN_FILE = STATE_DIR / "feed-last-seen.txt"
LOG_FILE = OPS_DIR / "feed-poll.jsonl"
RUNTIME_DB = DATA_ROOT / "runtime" / "rick-runtime.db"

PROOF_SOURCES = {"belkins", "folderly", "vlad-newsletter", "vladsnewsletter", "vlads-newsletter"}
ALGO_SOURCE = "shir-man"
HIGH_PRIORITY_THRESHOLD = 0.9
ROUNDUP_MIN_THRESHOLD = 0.75


# ---------------------------------------------------------------------------
# I/O helpers (all defensive, never raise)
# ---------------------------------------------------------------------------

def _ensure_dirs() -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        OPS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _read_last_seen() -> int:
    try:
        if LAST_SEEN_FILE.exists():
            txt = LAST_SEEN_FILE.read_text(encoding="utf-8").strip()
            if txt and txt.lstrip("-").isdigit():
                return int(txt)
    except Exception:
        pass
    return 0


def _write_last_seen(value: int) -> None:
    try:
        _ensure_dirs()
        LAST_SEEN_FILE.write_text(str(int(value)), encoding="utf-8")
    except Exception:
        pass


def _log(event: str, **fields: Any) -> None:
    """Append a JSONL log line. Best-effort, never raises."""
    try:
        _ensure_dirs()
        rec = {"ts": datetime.utcnow().isoformat(timespec="seconds") + "Z", "event": event, **fields}
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _classify_source(source: str | None) -> str:
    if not source:
        return "unknown"
    s = str(source).lower().strip()
    if s in PROOF_SOURCES:
        return "proof"
    if s == ALGO_SOURCE:
        return ALGO_SOURCE
    # tolerate variants
    for proof in PROOF_SOURCES:
        if proof in s:
            return "proof"
    if "shir-man" in s or "shirman" in s:
        return ALGO_SOURCE
    return s


def _get_score(item: dict[str, Any]) -> float:
    for key in ("sm_algo_score", "algo_score", "score"):
        v = item.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


# ---------------------------------------------------------------------------
# Workflow dispatch (queue rows in rick-runtime.db when LIVE)
# ---------------------------------------------------------------------------

def _queue_workflow(kind: str, context: dict[str, Any], priority: int, live: bool) -> str:
    """Queue a workflow row in rick-runtime.db. Returns workflow id (or
    'dry-run-<uuid>' when not live)."""
    wf_id = uuid.uuid4().hex
    if not live:
        return f"dry-run-{wf_id[:12]}"
    if not RUNTIME_DB.exists():
        # Daemon hasn't booted yet, or fresh install — log and skip.
        _log("queue.skip.no_db", kind=kind, db=str(RUNTIME_DB))
        return f"no-db-{wf_id[:12]}"
    try:
        conn = sqlite3.connect(str(RUNTIME_DB), timeout=8)
        try:
            conn.row_factory = sqlite3.Row
            now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            ctx_json = json.dumps(context, ensure_ascii=False)
            # Defensive: schema may have evolved. Try the known shape; if
            # column mismatch, log and skip rather than crash.
            conn.execute(
                """
                INSERT INTO workflows (id, kind, status, priority, context_json, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, ?, ?, ?)
                """,
                (wf_id, kind, int(priority), ctx_json, now, now),
            )
            conn.commit()
            return wf_id
        finally:
            conn.close()
    except sqlite3.Error as e:
        _log("queue.db_error", kind=kind, error=str(e)[:200])
        return f"db-err-{wf_id[:12]}"
    except Exception as e:
        _log("queue.unexpected_error", kind=kind, error=str(e)[:200])
        return f"err-{wf_id[:12]}"


def _append_roundup(item: dict[str, Any]) -> Path | None:
    """Append item to today's roundup queue (one JSONL per day)."""
    try:
        _ensure_dirs()
        day = datetime.utcnow().strftime("%Y-%m-%d")
        path = STATE_DIR / f"roundup-queue-{day}.jsonl"
        rec = {
            "queued_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "item": {
                "id": item.get("id"),
                "source": item.get("source"),
                "title": item.get("title"),
                "url": item.get("url"),
                "summary": (item.get("summary") or "")[:1000],
                "sm_algo_score": _get_score(item),
                "published_at": item.get("published_at"),
            },
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return path
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _dispatch_one(item: dict[str, Any], live: bool) -> tuple[str, str | None]:
    """Decide what to do with one item. Returns (action, workflow_id)."""
    src_class = _classify_source(item.get("source"))
    score = _get_score(item)
    item_id = item.get("id")

    base_ctx = {
        "trigger_payload": {
            "feed_item": {
                "id": item_id,
                "source": item.get("source"),
                "source_kind": item.get("source_kind"),
                "title": item.get("title"),
                "url": item.get("url"),
                "summary": item.get("summary"),
                "sm_algo_score": score,
                "published_at": item.get("published_at"),
            },
            "trigger_source": "feed-poll",
        },
    }

    # Belkins / Folderly / Newsletter — always proof_repurpose.
    if src_class == "proof":
        wf_id = _queue_workflow("proof_repurpose", base_ctx, priority=50, live=live)
        return ("proof_repurpose", wf_id)

    # shir-man algo-scored items.
    if src_class == ALGO_SOURCE:
        if score > HIGH_PRIORITY_THRESHOLD:
            wf_id = _queue_workflow("feed_riff", base_ctx, priority=90, live=live)
            return ("feed_riff_high", wf_id)
        if score >= ROUNDUP_MIN_THRESHOLD:
            path = _append_roundup(item)
            return ("roundup_queued", str(path) if path else None)
        return ("skipped_low_score", None)

    # Unknown source — be conservative, just log.
    return ("skipped_unknown_source", None)


def main() -> int:
    parser = argparse.ArgumentParser(description="RSS feed poller — TIER-3.6")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run (no DB writes, no mark-processed)")
    parser.add_argument("--max-items", type=int, default=50, help="Max items to process this run (default 50)")
    args = parser.parse_args()

    _ensure_dirs()

    env_live = os.getenv("RICK_FEED_POLL_LIVE", "0").strip() == "1"
    live = env_live and not args.dry_run

    _log("run.start", live=live, dry_run=args.dry_run, env_live=env_live, max_items=args.max_items)

    # Lazy import so a broken integration module never crashes argparse.
    try:
        from runtime.integrations.rss_client import fetch_recent, mark_processed
    except Exception as e:
        _log("import.error", error=str(e)[:200])
        print(f"feed-poll: cannot import rss_client: {e}", file=sys.stderr)
        return 0  # not a failure — daemon must keep running

    last_seen = _read_last_seen()
    _log("state.last_seen", last_seen=last_seen)

    try:
        items = fetch_recent(since_id=last_seen if last_seen > 0 else None, limit=int(args.max_items))
    except Exception as e:
        _log("fetch.error", error=str(e)[:200])
        items = []

    if not items:
        # Service unreachable (404/503 — not deployed yet) OR no new items.
        _log("run.no_items", live=live)
        print("feed-poll: 0 new items (service may not be deployed yet — this is OK)")
        return 0

    print(f"feed-poll: fetched {len(items)} items (live={live})")

    counters = {
        "feed_riff_high": 0,
        "roundup_queued": 0,
        "proof_repurpose": 0,
        "skipped_low_score": 0,
        "skipped_unknown_source": 0,
    }
    queued_ids: list[int] = []
    max_id_seen = last_seen

    for item in items:
        try:
            item_id = item.get("id")
            if isinstance(item_id, int) and item_id > max_id_seen:
                max_id_seen = item_id
            elif isinstance(item_id, str) and item_id.lstrip("-").isdigit():
                if int(item_id) > max_id_seen:
                    max_id_seen = int(item_id)

            action, wf_id = _dispatch_one(item, live=live)
            counters[action] = counters.get(action, 0) + 1
            _log(
                "dispatch",
                action=action,
                workflow_id=wf_id,
                item_id=item_id,
                source=item.get("source"),
                score=_get_score(item),
                title=(item.get("title") or "")[:120],
            )
            if action in ("feed_riff_high", "roundup_queued", "proof_repurpose") and item_id is not None:
                try:
                    queued_ids.append(int(item_id))
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            _log("dispatch.error", error=str(e)[:200], item_id=item.get("id"))

    # Persist new last-seen even when dry-running so we don't loop the same
    # batch forever in dry-run testing. (Vlad can manually reset by editing
    # ~/rick-vault/state/feed-last-seen.txt back to 0.)
    if max_id_seen > last_seen:
        _write_last_seen(max_id_seen)
        _log("state.advance", from_id=last_seen, to_id=max_id_seen)

    # mark-processed only when LIVE and we actually queued something.
    if live and queued_ids:
        try:
            ok = mark_processed(queued_ids)
            _log("mark_processed", ok=bool(ok), n=len(queued_ids))
        except Exception as e:
            _log("mark_processed.error", error=str(e)[:200])

    summary = (
        f"feed-poll: dispatched riffs={counters['feed_riff_high']} "
        f"roundup={counters['roundup_queued']} "
        f"proof={counters['proof_repurpose']} "
        f"skipped_low={counters['skipped_low_score']} "
        f"skipped_unknown={counters['skipped_unknown_source']} "
        f"(live={live})"
    )
    print(summary)
    _log("run.done", live=live, counters=counters, last_seen=max_id_seen)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        _log("run.crash", error=str(e)[:300])
        print(f"feed-poll: fatal {e}", file=sys.stderr)
        # Exit 0 so launchd doesn't mark the agent as crashed.
        raise SystemExit(0)
