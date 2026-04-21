#!/usr/bin/env python3
"""Daily analytics ingest — stores GA4 / GSC / Ahrefs / Lighthouse metrics
for meetrick.ai into analytics_snapshots so the weekly newsletter + any
other dashboard can query them via SQL.

MVP scope: Lighthouse (Google PageSpeed Insights public API, no auth).
MCP-backed sources (GA4 via Windsor.ai, GSC + Ahrefs via their MCPs) are
NOT callable from cron — they run in Claude Code agent sessions and drop
JSONL cache files at ~/rick-vault/analytics/cache/<source>-YYYY-MM-DD.jsonl.
This script reads those caches + merges them into analytics_snapshots.

So: the cron populates what it can (Lighthouse), and every time you run
a "refresh analytics" Claude session, the richer data gets pulled in too.

Gated by RICK_ANALYTICS_LIVE=1. Default dry-run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.db import connect as db_connect  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
CACHE_DIR = DATA_ROOT / "analytics" / "cache"
LOG_FILE = DATA_ROOT / "operations" / "analytics-ingest.jsonl"

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
TARGET_URL = os.getenv("ANALYTICS_TARGET_URL", "https://meetrick.ai")


def _today() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def fetch_lighthouse(url: str = TARGET_URL, strategy: str = "mobile") -> dict:
    """Call PageSpeed Insights. Returns scored metrics + core web vitals."""
    params = {"url": url, "strategy": strategy, "category": "performance"}
    full = PSI_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": "rick-analytics/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"http-{exc.code}", "body": (exc.read() if exc.fp else b"").decode("utf-8", "ignore")[:200]}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    try:
        lr = body.get("lighthouseResult", {})
        cat = lr.get("categories", {}).get("performance", {})
        audits = lr.get("audits", {})
        def _val(audit_id, key="numericValue"):
            a = audits.get(audit_id) or {}
            return a.get(key)
        return {
            "url": url,
            "strategy": strategy,
            "performance": float(cat.get("score") or 0) * 100.0,
            "lcp_ms": _val("largest-contentful-paint"),
            "fid_ms": _val("max-potential-fid"),
            "cls": _val("cumulative-layout-shift"),
            "tbt_ms": _val("total-blocking-time"),
            "fetched_at": _now_iso(),
        }
    except Exception as exc:
        return {"error": f"parse: {exc}"}


def load_cache_file(source: str) -> list[dict]:
    """Read today's MCP-populated cache for a given source (ga4|gsc|ahrefs)."""
    path = CACHE_DIR / f"{source}-{_today()}.jsonl"
    if not path.exists():
        return []
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return rows


def _insert(conn, source: str, metric_name: str, value: float | None,
            value_str: str = "", dims: dict | None = None):
    conn.execute(
        """
        INSERT INTO analytics_snapshots
          (source, metric_name, metric_value, metric_str, dim_json,
           snapshot_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source, metric_name, value, value_str,
         json.dumps(dims or {}), _today(), _now_iso()),
    )


def ingest(dry_run: bool = False) -> dict:
    conn = db_connect()
    summary = {"source_counts": {}, "errors": []}
    try:
        # 1. Lighthouse (live, no MCP required)
        lh = fetch_lighthouse()
        if "error" in lh:
            summary["errors"].append(f"lighthouse: {lh['error']}")
        else:
            entries = [
                ("performance", float(lh["performance"] or 0), "", {"url": lh["url"], "strategy": lh["strategy"]}),
                ("lcp_ms", float(lh.get("lcp_ms") or 0), "", {}),
                ("cls", float(lh.get("cls") or 0), "", {}),
                ("tbt_ms", float(lh.get("tbt_ms") or 0), "", {}),
            ]
            if not dry_run:
                for name, val, vs, dims in entries:
                    _insert(conn, "lighthouse", name, val, vs, dims)
            summary["source_counts"]["lighthouse"] = len(entries)
        # 2. Cache-backed sources (populated by a Claude MCP session)
        for source in ("ga4", "gsc", "ahrefs"):
            rows = load_cache_file(source)
            if not rows:
                continue
            if not dry_run:
                for row in rows:
                    _insert(
                        conn, source,
                        str(row.get("metric_name") or "unknown"),
                        float(row.get("metric_value") or 0) if row.get("metric_value") is not None else None,
                        str(row.get("metric_str") or "")[:400],
                        row.get("dims") or {},
                    )
            summary["source_counts"][source] = len(rows)
        if not dry_run:
            conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return summary


def _log(event: dict):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ran_at": _now_iso(), **event}) + "\n")
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    args = ap.parse_args()

    if not args.dry_run and os.getenv("RICK_ANALYTICS_LIVE") != "1":
        args.dry_run = True

    result = ingest(dry_run=args.dry_run)
    _log({"dry_run": args.dry_run, **result})
    print(json.dumps({"dry_run": args.dry_run, **result}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
