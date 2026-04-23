"""Hive client — Rick → meetrick-api hive endpoints.

Stdlib-only HTTPS POST/GET wrappers. Used by scripts/hive-sync.py for the
daily export-wins + import-global-best round trip.

Auth: rick_id + rick_secret from rick.env. Gracefully no-ops if either is
unset (returns None) so the daily cron doesn't error during the window
when Rick Prime is registered but not yet authorized.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE = os.getenv("MEETRICK_API_BASE", "https://api.meetrick.ai")
USER_AGENT = "Rick-HiveClient/1.0"


def _credentials() -> tuple[str | None, str | None]:
    return os.getenv("RICK_ID") or None, os.getenv("RICK_SECRET") or None


def _request(method: str, path: str, body: dict | None = None, timeout: int = 12) -> dict | None:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"_raw": raw[:400], "_status": resp.status}
    except urllib.error.HTTPError as e:
        try:
            body_text = e.read().decode("utf-8", errors="replace")
            try:
                return {"ok": False, "_status": e.code, **json.loads(body_text)}
            except json.JSONDecodeError:
                return {"ok": False, "_status": e.code, "_raw": body_text[:400]}
        except Exception:
            return {"ok": False, "_status": e.code}
    except (urllib.error.URLError, TimeoutError):
        return None


def post_learning(
    skill_name: str,
    variant_id: str,
    prompt_text: str,
    win_rate: float,
    n_runs: int,
    sum_cost_usd: float = 0.0,
) -> dict | None:
    """Share a winning variant with the hive. Returns API response or None."""
    rick_id, rick_secret = _credentials()
    if not rick_id or not rick_secret:
        return {"ok": False, "skip": True, "reason": "RICK_ID/RICK_SECRET unset"}
    return _request("POST", "/api/v1/hive/learnings", {
        "rick_id": rick_id,
        "rick_secret": rick_secret,
        "skill_name": skill_name,
        "variant_id": variant_id,
        "prompt_text": prompt_text,
        "win_rate": float(win_rate),
        "n_runs": int(n_runs),
        "sum_cost_usd": float(sum_cost_usd),
    })


def get_global_best(skills: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Pull the top variants per skill across the fleet. Returns {skill: [variant,...]}."""
    if not skills:
        return {}
    q = ",".join(s for s in skills if s)
    res = _request("GET", f"/api/v1/hive/global-best?skills={urllib.parse.quote(q)}")
    if not res or not res.get("ok"):
        return {}
    return res.get("by_skill", {}) or {}


def get_stats() -> dict | None:
    return _request("GET", "/api/v1/hive/stats")
