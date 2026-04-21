#!/usr/bin/env python3
"""Hive heartbeat — periodic ping from this Rick to api.meetrick.ai.

POSTs /heartbeat with rick_id + rick_secret + version + diagnostics so the
mothership knows this Rick is alive and what it's been up to in the last
24h. Also touches ~/.openclaw/.hive-id so install.sh's beacon stays fresh.

Gated by RICK_HIVE_ENABLED=1. Without the flag, runs --dry-run regardless
of the CLI flag, so a misconfigured cron can't leak traffic.

Scheduled every 30min via ai.rick.hive-heartbeat.plist. Must never fail
hard — network outages + schema drifts + missing env vars all degrade to
logged warnings + exit 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DB_FILE = DATA_ROOT / "runtime" / "rick-runtime.db"
LOG_FILE = DATA_ROOT / "operations" / "hive-heartbeat.jsonl"
ENV_FILE = Path(os.getenv("RICK_ENV_FILE", str(Path.home() / "clawd" / "config" / "rick.env")))
HIVE_ID_FILE = Path(os.getenv("RICK_HIVE_ID_FILE", str(Path.home() / ".openclaw" / ".hive-id")))


def _load_env():
    """Load rick.env key=value pairs into os.environ (idempotent)."""
    if not ENV_FILE.exists():
        return
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def _api_base() -> str:
    base = os.getenv("MEETRICK_API_URL", "https://api.meetrick.ai/api/v1").rstrip("/")
    if not base.endswith("/api/v1"):
        base = base + "/api/v1"
    return base


def _open_db():
    try:
        if not DB_FILE.exists():
            return None
        c = sqlite3.connect(str(DB_FILE), timeout=5.0)
        c.row_factory = sqlite3.Row
        return c
    except Exception:
        return None


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _gather_diagnostics() -> dict:
    c = _open_db()
    if c is None:
        return {"db": "unavailable"}
    diag = {}
    diag["cost_24h"] = _safe(
        lambda: float(c.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM outcomes WHERE created_at > datetime('now','-1 day')"
        ).fetchone()[0] or 0.0),
        0.0,
    )
    diag["workflows_done_24h"] = _safe(
        lambda: int(c.execute(
            "SELECT COUNT(*) FROM workflows WHERE status='done' AND updated_at > datetime('now','-1 day')"
        ).fetchone()[0]),
        0,
    )
    diag["workflows_failed_24h"] = _safe(
        lambda: int(c.execute(
            "SELECT COUNT(*) FROM workflows WHERE status='failed' AND updated_at > datetime('now','-1 day')"
        ).fetchone()[0]),
        0,
    )
    try:
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM subagent_heartbeat GROUP BY status"
        ).fetchall()
        diag["subagents"] = {r["status"]: r["n"] for r in rows}
    except Exception:
        diag["subagents"] = {}
    try:
        mtime = DB_FILE.stat().st_mtime
        diag["db_age_s"] = max(0, int(time.time() - mtime))
    except Exception:
        diag["db_age_s"] = -1
    try:
        c.close()
    except Exception:
        pass
    return diag


def _touch_hive_id():
    try:
        HIVE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not HIVE_ID_FILE.exists():
            HIVE_ID_FILE.write_text("", encoding="utf-8")
        else:
            HIVE_ID_FILE.touch()
    except OSError:
        pass


def _version() -> str:
    vf = Path.home() / "clawd" / "VERSION"
    if vf.exists():
        try:
            return vf.read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            return "unknown"
    try:
        import subprocess
        r = subprocess.run(
            ["git", "-C", str(Path.home() / ".openclaw" / "workspace"), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _log(event: dict):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ran_at": datetime.now().isoformat(timespec="seconds"), **event}) + "\n")
    except OSError:
        pass


def _post(payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _api_base() + "/heartbeat",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "rick-heartbeat/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return {"status": resp.status, "body": resp.read().decode("utf-8", "ignore")[:300]}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": (exc.read() if exc.fp else b"").decode("utf-8", "ignore")[:300]}
    except Exception as exc:
        return {"status": 0, "error": f"{type(exc).__name__}: {exc}"}


def main():
    _load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="compute + log but do not POST")
    args = ap.parse_args()

    live = os.getenv("RICK_HIVE_ENABLED") == "1"
    dry = args.dry_run or not live

    rick_id = os.getenv("RICK_ID", "").strip()
    rick_secret = os.getenv("RICK_SECRET", "").strip()
    if not rick_id or not rick_secret:
        _log({"result": "skip", "reason": "no rick_id/secret"})
        print(json.dumps({"status": "skip", "reason": "no rick_id/secret"}))
        return 0

    _touch_hive_id()
    payload = {
        "rick_id": rick_id,
        "rick_secret": rick_secret,
        "version": _version(),
        "diagnostics": _gather_diagnostics(),
    }
    if dry:
        safe_payload = {**payload, "rick_secret": "***"}
        _log({"result": "dry-run", "payload": safe_payload})
        print(json.dumps({"status": "dry-run", "payload": safe_payload}, indent=2))
        return 0
    result = _post(payload)
    _log({"result": "posted", "response": result})
    print(json.dumps({"status": "posted", "response": result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
