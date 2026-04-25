#!/usr/bin/env python3
"""Load-test harness for runtime/outbound_dispatcher.py.

Populates a synthetic queue, drives drain() repeatedly, captures per-drain
wall time, emits P50/P95/P99 latency + throughput, writes a JSON report.

Why this exists: outbound_dispatcher is the single drain point for ALL of
Rick's outbound (moltbook, threads, instagram, reddit, linkedin, blog).
Currently exercised at ~1 job/min. If/when 3 channels light up, it'll face
3-5x load. We need to find the throughput ceiling before production does.

Manual-trigger only — no LaunchAgent, no cron. Run before scaling decisions.

Usage:
    python3 scripts/loadtest-outbound-dispatcher.py --depth 100 --drains 20

Safety:
    - Uses synthetic-loadtest channel (no formatter resolves to it → dispatcher
      marks status='skipped', exercises full SQL/kill-switch path without sending)
    - --channel guards against real channel names so a typo can't fire real sends
    - Cleanup runs in try/finally so synthetic rows are always removed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.db import connect  # noqa: E402
from runtime import outbound_dispatcher  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))

# Real channels — guard against accidentally seeding into one. The dispatcher
# would route to a real formatter and (if the LIVE flag is set) send for real.
REAL_CHANNELS = {
    "moltbook", "threads", "instagram", "reddit", "linkedin",
    "blog", "email", "x", "x_twitter", "twitter", "newsletter",
    "hackernews", "hn",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile. Stable, no interpolation surprises."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    rank = max(0, min(n - 1, int(round((pct / 100.0) * n)) - 1))
    return sorted_values[rank]


def seed_queue(conn, channel: str, depth: int) -> int:
    """Direct INSERT (NOT fan_out) — fan_out dedupes by (lead_id, channel,
    template_id) over 7d, which would silently no-op repeated synthetic seeds.
    """
    now = _now_iso()
    inserted = 0
    for _ in range(depth):
        job_id = f"loadtest_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO outbound_jobs
                (id, lead_id, channel, template_id, payload_json,
                 status, scheduled_at, attempts, created_at)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, 0, ?)
            """,
            (job_id, f"loadtest_lead_{uuid.uuid4().hex[:8]}", channel,
             f"loadtest_tpl_{uuid.uuid4().hex[:8]}",
             json.dumps({"body": "loadtest payload", "synthetic": True}),
             now, now),
        )
        inserted += 1
    conn.commit()
    return inserted


def cleanup_synthetic(conn, channel: str) -> int:
    """Remove synthetic rows. Called from finally — always runs."""
    cur = conn.execute(
        "DELETE FROM outbound_jobs WHERE channel = ?",
        (channel,),
    )
    deleted = cur.rowcount
    conn.commit()
    return deleted


def run(depth: int, drains: int, batch_size: int, channel: str,
        dry_run: bool, do_cleanup: bool) -> dict:
    if channel.strip().lower() in REAL_CHANNELS:
        raise SystemExit(
            f"refusing to load-test channel '{channel}' — resolves to a real channel name. "
            f"Use --channel synthetic-loadtest (or any non-real name)."
        )

    started_at = _now_iso()
    conn = connect()
    rows_before = conn.execute("SELECT COUNT(*) AS c FROM outbound_jobs").fetchone()["c"]
    seeded = seed_queue(conn, channel, depth)

    latencies_ms: list[float] = []
    summary_by_status_total: dict[str, int] = {}
    total_processed = 0
    total_picked = 0

    try:
        for _ in range(drains):
            t0 = time.monotonic()
            result = outbound_dispatcher.drain(conn=conn, batch_size=batch_size, dry_run=dry_run)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)
            total_processed += result.get("processed", 0)
            total_picked += result.get("picked", 0)
            for status, count in (result.get("summary_by_status") or {}).items():
                summary_by_status_total[status] = summary_by_status_total.get(status, 0) + count
            # If the drain came back empty, no point hammering — break early.
            if result.get("picked", 0) == 0:
                break
    finally:
        deleted = cleanup_synthetic(conn, channel) if do_cleanup else 0
        rows_after = conn.execute("SELECT COUNT(*) AS c FROM outbound_jobs").fetchone()["c"]
        conn.close()

    finished_at = _now_iso()
    sorted_lat = sorted(latencies_ms)
    p50 = _percentile(sorted_lat, 50)
    p95 = _percentile(sorted_lat, 95)
    p99 = _percentile(sorted_lat, 99)
    total_wall_s = sum(latencies_ms) / 1000.0
    jobs_per_s = (total_processed / total_wall_s) if total_wall_s > 0 else 0.0

    report = {
        "meta": {
            "started_at": started_at,
            "finished_at": finished_at,
            "depth": depth,
            "drains_requested": drains,
            "drains_executed": len(latencies_ms),
            "batch_size": batch_size,
            "channel": channel,
            "dry_run": dry_run,
            "cleanup": do_cleanup,
        },
        "queue": {
            "rows_before": rows_before,
            "seeded": seeded,
            "synthetic_deleted": deleted,
            "rows_after": rows_after,
        },
        "drain": {
            "total_picked": total_picked,
            "total_processed": total_processed,
            "summary_by_status": summary_by_status_total,
        },
        "latency_ms": {
            "samples": len(latencies_ms),
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "p99": round(p99, 2),
            "min": round(min(latencies_ms), 2) if latencies_ms else 0,
            "max": round(max(latencies_ms), 2) if latencies_ms else 0,
            "mean": round(sum(latencies_ms) / len(latencies_ms), 2) if latencies_ms else 0,
        },
        "throughput": {
            "total_wall_s": round(total_wall_s, 3),
            "jobs_per_s": round(jobs_per_s, 2),
        },
    }
    return report


def render_summary(report: dict) -> str:
    m = report["meta"]
    lat = report["latency_ms"]
    tp = report["throughput"]
    drain = report["drain"]
    q = report["queue"]
    lines = [
        "## Outbound dispatcher load test",
        "",
        f"- channel: `{m['channel']}` | depth: {m['depth']} | drains: {m['drains_executed']}/{m['drains_requested']} | batch: {m['batch_size']} | dry_run: {m['dry_run']}",
        f"- seeded: {q['seeded']} | picked: {drain['total_picked']} | processed: {drain['total_processed']} | cleanup deleted: {q['synthetic_deleted']}",
        "",
        "### Per-drain wall time (ms)",
        "",
        "| samples | p50 | p95 | p99 | min | max | mean |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {lat['samples']} | {lat['p50']} | {lat['p95']} | {lat['p99']} | {lat['min']} | {lat['max']} | {lat['mean']} |",
        "",
        f"### Throughput: {tp['jobs_per_s']} jobs/s ({tp['total_wall_s']}s wall)",
        "",
        "### Status breakdown",
    ]
    for status, count in sorted(drain["summary_by_status"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- {status}: {count}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=100, help="synthetic jobs to enqueue")
    ap.add_argument("--drains", type=int, default=20, help="drain calls to make")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--channel", default="synthetic-loadtest")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    grp.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    ap.add_argument("--no-cleanup", dest="cleanup", action="store_false", default=True)
    args = ap.parse_args()

    report = run(
        depth=args.depth, drains=args.drains, batch_size=args.batch_size,
        channel=args.channel, dry_run=args.dry_run, do_cleanup=args.cleanup,
    )

    out_path = DATA_ROOT / "operations" / f"loadtest-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(render_summary(report))
    print(f"\nFull report: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
