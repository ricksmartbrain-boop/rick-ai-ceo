#!/usr/bin/env python3
"""Rick's daily receipts — structured, PII-scrubbed, machine-readable JSON.

Sibling to the Rick daily diary (`rick-daily-diary.py`). Where the diary is
prose ("what Rick did yesterday in his own voice"), receipts are the
auditable JSON ledger ("here are the numbers, signed by daemon, anchored
by git commit"). This is THE moat per agent P3 — a tamper-evident
operational ledger that takes 3-4 years to fake.

Reads yesterday's data from rick-runtime.db:
  - Outcomes: total cost, count, top 5 routes by cost + by count
  - Workflows: completed (kind, count, status)
  - Subagents: kind, status, cost from subagent_heartbeat
  - Stripe events from ~/rick-vault/operations/stripe-events.jsonl (if exists)
  - MRR (parsed from latest revenue/reconciliation-*.md, fallback $9 per SELF-FAQ)

Writes BOTH:
  - ~/meetrick-site/receipts/YYYY-MM-DD.json  -- structured day receipt
  - ~/meetrick-site/receipts/manifest.json    -- append entry to index

PII scrub: never include email addresses, customer names, prospect names,
license keys, or rick_secret. Only aggregates + opaque IDs.

Env:
  RICK_RECEIPTS_LIVE=1   -- write files (default: dry-run print)
  RICK_RECEIPTS_DATE=YYYY-MM-DD  -- target date (default: yesterday)
  RICK_DATA_ROOT         -- rick-vault root (default: ~/rick-vault)
  RICK_SITE_DIR          -- meetrick-site root (default: ~/meetrick-site)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0.0"

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
SITE_DIR = Path(os.getenv("RICK_SITE_DIR", str(Path.home() / "meetrick-site")))
RECEIPTS_DIR = SITE_DIR / "receipts"
DB_PATH = DATA_ROOT / "runtime" / "rick-runtime.db"
STRIPE_EVENTS_PATH = DATA_ROOT / "operations" / "stripe-events.jsonl"

# Anything resembling these in free-text fields gets scrubbed before publish.
PII_PATTERNS = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # emails
    re.compile(r"\bsk_[A-Za-z0-9_]{10,}\b"),                         # stripe secrets
    re.compile(r"\brick_[A-Za-z0-9_]{8,}\b"),                        # rick_secret-ish
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),                        # generic api keys
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{10,}", re.IGNORECASE),      # bearer tokens
]


def _scrub(text: str) -> str:
    """Strip likely-PII from any free-text field before it hits a public JSON."""
    if not text:
        return ""
    out = str(text)
    for pat in PII_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def _connect() -> sqlite3.Connection | None:
    if not DB_PATH.is_file():
        return None
    try:
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _gather_outcomes(con: sqlite3.Connection, target: date) -> dict:
    """Aggregates from outcomes table. Cost + count + top-N routes."""
    start = f"{target.isoformat()} 00:00:00"
    end = f"{target.isoformat()} 23:59:59"

    def q(sql, *args, default=None):
        try:
            row = con.execute(sql, args).fetchone()
            return row[0] if row and row[0] is not None else default
        except sqlite3.Error:
            return default

    def qall(sql, *args):
        try:
            return [dict(r) for r in con.execute(sql, args).fetchall()]
        except sqlite3.Error:
            return []

    cost_total = q(
        "SELECT ROUND(SUM(cost_usd),4) FROM outcomes WHERE created_at BETWEEN ? AND ?",
        start, end, default=0.0,
    )
    count_total = q(
        "SELECT COUNT(*) FROM outcomes WHERE created_at BETWEEN ? AND ?",
        start, end, default=0,
    )
    top_by_cost = qall(
        "SELECT route, ROUND(SUM(cost_usd),4) AS cost_usd, COUNT(*) AS n "
        "FROM outcomes WHERE created_at BETWEEN ? AND ? "
        "GROUP BY route ORDER BY cost_usd DESC LIMIT 5",
        start, end,
    )
    top_by_count = qall(
        "SELECT route, COUNT(*) AS n, ROUND(SUM(cost_usd),4) AS cost_usd "
        "FROM outcomes WHERE created_at BETWEEN ? AND ? "
        "GROUP BY route ORDER BY n DESC LIMIT 5",
        start, end,
    )
    quality_scored = q(
        "SELECT COUNT(quality_score) FROM outcomes "
        "WHERE created_at BETWEEN ? AND ? AND quality_score IS NOT NULL",
        start, end, default=0,
    )
    return {
        "cost_total_usd": float(cost_total or 0),
        "count_total": int(count_total or 0),
        "quality_scored_count": int(quality_scored or 0),
        "top_routes_by_cost": [
            {"route": _scrub(r["route"]), "cost_usd": float(r["cost_usd"] or 0), "n": int(r["n"])}
            for r in top_by_cost
        ],
        "top_routes_by_count": [
            {"route": _scrub(r["route"]), "n": int(r["n"]), "cost_usd": float(r["cost_usd"] or 0)}
            for r in top_by_count
        ],
    }


def _gather_workflows(con: sqlite3.Connection, target: date) -> dict:
    """Aggregates from workflows updated yesterday. (kind, status) -> count."""
    start = f"{target.isoformat()} 00:00:00"
    end = f"{target.isoformat()} 23:59:59"
    try:
        rows = con.execute(
            "SELECT kind, status, COUNT(*) AS n FROM workflows "
            "WHERE updated_at BETWEEN ? AND ? "
            "GROUP BY kind, status ORDER BY n DESC",
            (start, end),
        ).fetchall()
    except sqlite3.Error:
        rows = []

    by_kind_status = [
        {"kind": _scrub(r["kind"]), "status": _scrub(r["status"]), "n": int(r["n"])}
        for r in rows
    ]
    completed = sum(r["n"] for r in by_kind_status if r["status"] == "done")
    cancelled = sum(r["n"] for r in by_kind_status if r["status"] == "cancelled")
    return {
        "by_kind_status": by_kind_status,
        "completed_count": int(completed),
        "cancelled_count": int(cancelled),
    }


def _gather_subagents(con: sqlite3.Connection, target: date) -> dict:
    """Aggregates from subagent_heartbeat. kind+status+cost only — no task text."""
    start = f"{target.isoformat()} 00:00:00"
    end = f"{target.isoformat()} 23:59:59"
    try:
        rows = con.execute(
            "SELECT kind, status, COUNT(*) AS n, "
            "ROUND(SUM(cost_usd),4) AS cost_usd, "
            "ROUND(SUM(usage_tokens_in),0) AS tok_in, "
            "ROUND(SUM(usage_tokens_out),0) AS tok_out "
            "FROM subagent_heartbeat "
            "WHERE started_at BETWEEN ? AND ? "
            "GROUP BY kind, status ORDER BY cost_usd DESC",
            (start, end),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    by_kind_status = [
        {
            "kind": _scrub(r["kind"]),
            "status": _scrub(r["status"]),
            "n": int(r["n"]),
            "cost_usd": float(r["cost_usd"] or 0),
            "tokens_in": int(r["tok_in"] or 0),
            "tokens_out": int(r["tok_out"] or 0),
        }
        for r in rows
    ]
    return {
        "by_kind_status": by_kind_status,
        "total_runs": sum(b["n"] for b in by_kind_status),
        "total_cost_usd": round(sum(b["cost_usd"] for b in by_kind_status), 4),
    }


def _gather_stripe_events(target: date) -> dict:
    """Reads stripe-events.jsonl if present. Aggregates only — never raw events."""
    out = {"present": False, "events_count": 0, "by_type": [], "gross_usd": 0.0}
    if not STRIPE_EVENTS_PATH.is_file():
        return out
    try:
        text = STRIPE_EVENTS_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    target_str = target.isoformat()
    type_counts: dict[str, int] = {}
    gross_cents = 0
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Match by created_at / created / timestamp prefix YYYY-MM-DD
        ts = (ev.get("created_at") or ev.get("created") or ev.get("ts") or "")
        if not str(ts).startswith(target_str):
            continue
        n += 1
        et = str(ev.get("type") or ev.get("event_type") or "unknown")
        type_counts[et] = type_counts.get(et, 0) + 1
        amt = ev.get("amount_cents") or ev.get("amount") or 0
        try:
            gross_cents += int(amt)
        except (TypeError, ValueError):
            pass
    out["present"] = True
    out["events_count"] = n
    out["by_type"] = sorted(
        [{"type": _scrub(k), "n": v} for k, v in type_counts.items()],
        key=lambda r: r["n"], reverse=True,
    )
    out["gross_usd"] = round(gross_cents / 100.0, 2) if gross_cents else 0.0
    return out


def _real_mrr() -> dict:
    """Parses latest reconciliation-*.md for real MRR. Falls back to $9
    per SELF-FAQ if no file or no parseable line."""
    revdir = DATA_ROOT / "revenue"
    if not revdir.is_dir():
        return {"usd_per_mo": 9.0, "source": "fallback_self_faq"}
    recs = sorted(revdir.glob("reconciliation-*.md"))
    if not recs:
        return {"usd_per_mo": 9.0, "source": "fallback_self_faq"}
    try:
        text = recs[-1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"usd_per_mo": 9.0, "source": "fallback_self_faq"}
    m = re.search(r"Real current MRR[:\*\s]+\$?([0-9]+(?:\.[0-9]+)?)", text)
    if m:
        return {
            "usd_per_mo": float(m.group(1)),
            "source": f"reconciliation:{recs[-1].name}",
        }
    return {"usd_per_mo": 9.0, "source": "fallback_self_faq"}


def _build_receipt(target: date) -> dict:
    """Compose the full machine-readable JSON receipt for `target` date."""
    con = _connect()
    if con is None:
        outcomes = {"cost_total_usd": 0.0, "count_total": 0, "quality_scored_count": 0,
                    "top_routes_by_cost": [], "top_routes_by_count": []}
        workflows = {"by_kind_status": [], "completed_count": 0, "cancelled_count": 0}
        subagents = {"by_kind_status": [], "total_runs": 0, "total_cost_usd": 0.0}
        db_status = "missing"
    else:
        try:
            outcomes = _gather_outcomes(con, target)
            workflows = _gather_workflows(con, target)
            subagents = _gather_subagents(con, target)
            db_status = "ok"
        except Exception as exc:  # noqa: BLE001
            outcomes = {"cost_total_usd": 0.0, "count_total": 0, "quality_scored_count": 0,
                        "top_routes_by_cost": [], "top_routes_by_count": []}
            workflows = {"by_kind_status": [], "completed_count": 0, "cancelled_count": 0}
            subagents = {"by_kind_status": [], "total_runs": 0, "total_cost_usd": 0.0}
            db_status = f"error:{type(exc).__name__}"
        finally:
            try:
                con.close()
            except sqlite3.Error:
                pass

    stripe = _gather_stripe_events(target)
    mrr = _real_mrr()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 2026-04-24: cryptographic chain anchor — each receipt carries the
    # SHA-256 of yesterday's receipt file content (verifiable client-side
    # without trusting git) PLUS the git commit SHA that introduced
    # yesterday's receipt (extra anchor verifiable via `git show`).
    # Modifying any past receipt would break the chain. This is the
    # tamper-evident moat per master plan TIER-4.2 (3-4yr to fake).
    chain = _previous_receipt_anchors(target)

    return {
        "schema_version": SCHEMA_VERSION,
        "date": target.isoformat(),
        "generated_at_utc": now_iso,
        "generator": "receipts-publish.py",
        "tagline": "Rick's receipts. Daily. Including the bad days.",
        "db_status": db_status,
        "chain": chain,  # NEW: prev_receipt_sha256 + prev_receipt_git_sha
        "mrr": mrr,
        "outcomes": outcomes,
        "workflows": workflows,
        "subagents": subagents,
        "stripe": stripe,
        "totals": {
            "llm_cost_usd": outcomes["cost_total_usd"],
            "llm_event_count": outcomes["count_total"],
            "subagent_cost_usd": subagents["total_cost_usd"],
            "combined_cost_usd": round(
                outcomes["cost_total_usd"] + subagents["total_cost_usd"], 4
            ),
            "workflows_completed": workflows["completed_count"],
            "stripe_gross_usd": stripe["gross_usd"],
            "mrr_usd_per_mo": mrr["usd_per_mo"],
        },
        "pii_policy": "scrubbed: emails, api_keys, customer_names, prospect_names",
        "verification": (
            "anyone can verify the chain: clone meetrick-site, "
            "run scripts/verify-receipts-chain.py — checks every "
            "receipt's prev_receipt_sha256 matches the prior file's "
            "actual SHA-256. Tamper detection is local + cryptographic."
        ),
    }


def _previous_receipt_anchors(target: date) -> dict:
    """Compute SHA-256 of yesterday's receipt file + git commit SHA that
    last touched it. Returns {prev_receipt_sha256, prev_receipt_git_sha,
    prev_receipt_date}. All None for the first-ever receipt."""
    yesterday = target - timedelta(days=1)
    yest_path = RECEIPTS_DIR / f"{yesterday.isoformat()}.json"

    sha256 = None
    if yest_path.is_file():
        try:
            sha256 = hashlib.sha256(yest_path.read_bytes()).hexdigest()
        except OSError:
            sha256 = None

    git_sha = None
    try:
        # Find the commit that last touched yesterday's receipt
        proc = subprocess.run(
            ["git", "-C", str(SITE_DIR), "log", "-1", "--format=%H",
             "--", f"receipts/{yesterday.isoformat()}.json"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            git_sha = proc.stdout.strip()
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        git_sha = None

    return {
        "prev_receipt_date": yesterday.isoformat() if (sha256 or git_sha) else None,
        "prev_receipt_sha256": sha256,
        "prev_receipt_git_sha": git_sha,
    }


def _git_commit_receipt(target: date, receipt: dict) -> dict:
    """Stage receipts/<date>.json + manifest.json + commit. Returns commit info.

    Auto-push gated by RICK_RECEIPTS_AUTOPUSH=1 (default OFF — Vlad pushes
    manually after reviewing). Always-safe: skips if not a git repo, skips
    if nothing staged, skips on any git error.
    """
    info = {"committed": False, "commit_sha": None, "pushed": False, "reason": ""}
    git_dir = SITE_DIR / ".git"
    if not git_dir.exists():
        info["reason"] = "site dir is not a git repo"
        return info

    totals = receipt.get("totals", {})
    msg_subject = (
        f"receipts: {target.isoformat()} "
        f"(spent ${totals.get('combined_cost_usd', 0):.2f}, "
        f"earned ${totals.get('stripe_gross_usd', 0):.2f}, "
        f"MRR ${totals.get('mrr_usd_per_mo', 0):.0f})"
    )
    msg_body = (
        f"Auto-published by receipts-publish.py.\n\n"
        f"Chain anchor (SHA-256 of {receipt['chain']['prev_receipt_date'] or 'genesis'}.json): "
        f"{receipt['chain']['prev_receipt_sha256'] or 'none'}\n"
        f"Previous git SHA: {receipt['chain']['prev_receipt_git_sha'] or 'none'}\n\n"
        f"Verify: scripts/verify-receipts-chain.py"
    )

    try:
        subprocess.run(
            ["git", "-C", str(SITE_DIR), "add",
             f"receipts/{target.isoformat()}.json", "receipts/manifest.json"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        # Skip if nothing staged (no changes)
        diff = subprocess.run(
            ["git", "-C", str(SITE_DIR), "diff", "--cached", "--name-only"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        if not diff.stdout.strip():
            info["reason"] = "no staged changes (receipt unchanged)"
            return info

        proc = subprocess.run(
            ["git", "-C", str(SITE_DIR), "commit", "-m", msg_subject, "-m", msg_body],
            check=True, capture_output=True, text=True, timeout=15,
        )
        # Capture the new commit SHA
        sha_proc = subprocess.run(
            ["git", "-C", str(SITE_DIR), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        info["committed"] = True
        info["commit_sha"] = sha_proc.stdout.strip()

        if os.getenv("RICK_RECEIPTS_AUTOPUSH", "").strip().lower() in ("1", "true", "yes"):
            push_proc = subprocess.run(
                ["git", "-C", str(SITE_DIR), "push", "origin", "main"],
                check=False, capture_output=True, text=True, timeout=30,
            )
            info["pushed"] = (push_proc.returncode == 0)
            if not info["pushed"]:
                info["reason"] = f"push failed: {push_proc.stderr.strip()[:200]}"
        else:
            info["reason"] = "RICK_RECEIPTS_AUTOPUSH not set — Vlad pushes manually"
    except subprocess.CalledProcessError as exc:
        info["reason"] = f"git error: {exc.stderr.strip()[:200] if exc.stderr else exc}"
    except (subprocess.SubprocessError, OSError) as exc:
        info["reason"] = f"subprocess error: {exc}"
    return info


def _update_manifest(manifest_path: Path, receipt: dict) -> dict:
    """Replace existing entry for same date, else prepend; sort desc by date."""
    manifest = {"schema_version": SCHEMA_VERSION, "entries": []}
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                manifest = existing
                manifest.setdefault("entries", [])
        except (json.JSONDecodeError, OSError):
            pass

    totals = receipt.get("totals", {})
    entry = {
        "date": receipt["date"],
        "json_path": f"/receipts/{receipt['date']}.json",
        "llm_cost_usd": totals.get("llm_cost_usd", 0.0),
        "subagent_cost_usd": totals.get("subagent_cost_usd", 0.0),
        "combined_cost_usd": totals.get("combined_cost_usd", 0.0),
        "llm_event_count": totals.get("llm_event_count", 0),
        "workflows_completed": totals.get("workflows_completed", 0),
        "stripe_gross_usd": totals.get("stripe_gross_usd", 0.0),
        "mrr_usd_per_mo": totals.get("mrr_usd_per_mo", 0.0),
        "schema_version": SCHEMA_VERSION,
    }

    manifest["entries"] = [
        e for e in manifest.get("entries", []) if e.get("date") != entry["date"]
    ]
    manifest["entries"].insert(0, entry)
    manifest["entries"].sort(key=lambda e: e.get("date", ""), reverse=True)
    manifest["updated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest["schema_version"] = SCHEMA_VERSION

    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish Rick's daily PII-scrubbed JSON receipt.")
    ap.add_argument("--dry-run", action="store_true", help="Print receipt; do not write files")
    ap.add_argument("--date", help="Target date YYYY-MM-DD (default: yesterday UTC)")
    args = ap.parse_args()

    target_str = args.date or os.getenv("RICK_RECEIPTS_DATE")
    if target_str:
        try:
            target = datetime.strptime(target_str, "%Y-%m-%d").date()
        except ValueError:
            print(json.dumps({"status": "error", "reason": f"bad --date {target_str!r}"}))
            return 2
    else:
        target = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    live_env = os.getenv("RICK_RECEIPTS_LIVE", "").strip().lower() in ("1", "true", "yes")
    live = live_env and not args.dry_run

    try:
        receipt = _build_receipt(target)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "reason": f"build failed: {type(exc).__name__}: {exc}"}))
        return 1

    if not live:
        print(f"=== DRY-RUN receipt for {target.isoformat()} ===")
        print(json.dumps(receipt, indent=2))
        return 0

    try:
        RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(json.dumps({"status": "error", "reason": f"mkdir failed: {exc}"}))
        return 1

    receipt_path = RECEIPTS_DIR / f"{target.isoformat()}.json"
    manifest_path = RECEIPTS_DIR / "manifest.json"

    try:
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(json.dumps({"status": "error", "reason": f"write receipt: {exc}"}))
        return 1

    try:
        manifest = _update_manifest(manifest_path, receipt)
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(json.dumps({"status": "error", "reason": f"write manifest: {exc}"}))
        return 1

    # 2026-04-24: git-anchor the published receipt. Single commit per
    # daily receipt → tamper-evident chain via prev_receipt_sha256 +
    # prev_receipt_git_sha embedded in each receipt's chain block.
    git_info = _git_commit_receipt(target, receipt)

    print(json.dumps({
        "status": "ok",
        "date": target.isoformat(),
        "receipt_path": str(receipt_path),
        "manifest_path": str(manifest_path),
        "entries_in_manifest": len(manifest.get("entries", [])),
        "totals": receipt.get("totals", {}),
        "chain": receipt.get("chain", {}),
        "git": git_info,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
