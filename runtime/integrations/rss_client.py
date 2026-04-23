"""RSS client — Rick → meetrick-rss-ingest service.

Stdlib-only HTTPS GET/POST wrappers for the meetrick-rss-ingest Railway
service (scaffolded at ~/meetrick-rss-ingest/, NOT YET DEPLOYED as of
2026-04-22). When deployed, exposes:

  GET  https://api.meetrick.ai/api/v1/feed/recent
  POST https://api.meetrick.ai/api/v1/feed/mark-processed   (X-Feed-Secret)

Env:
  MEETRICK_API_BASE         — default https://api.meetrick.ai
  RICK_FEED_INGEST_SECRET   — required for mark-processed; gracefully
                              skipped if unset (mark_processed returns False)

Design contract: NEVER raise — every public function wraps everything in
try/except and returns a safe empty-ish value on failure (404/503/timeout
all gracefully no-op so the daemon never crashes when the service is
unreachable, mid-deploy, or rate-limited).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE = os.getenv("MEETRICK_API_BASE", "https://api.meetrick.ai")
USER_AGENT = "Rick-RSSClient/1.0"
DEFAULT_TIMEOUT = 12


def _secret() -> str | None:
    s = os.getenv("RICK_FEED_INGEST_SECRET", "").strip()
    return s or None


def _request(
    method: str,
    path: str,
    body: dict | None = None,
    headers: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict | None:
    """Low-level HTTPS request. Returns parsed dict, or None on any error
    (including 404/503/timeout/DNS failure — caller treats None as "service
    unreachable, no-op gracefully")."""
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if data is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    try:
        req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"_raw": raw[:400], "_status": resp.status}
    except urllib.error.HTTPError as e:
        # 404 (service not deployed yet) / 503 (deploying) / 401 (bad secret)
        # — all return None so caller no-ops gracefully.
        try:
            body_text = e.read().decode("utf-8", errors="replace")
            return {"ok": False, "_status": e.code, "_raw": body_text[:400]}
        except Exception:
            return {"ok": False, "_status": e.code}
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return None
    except Exception:
        # Any other unexpected error — never crash daemon.
        return None


def fetch_recent(
    source: str | None = None,
    since_id: int | None = None,
    min_score: float | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Fetch recent feed items from the ingest service.

    Returns [] on any error (service down, network failure, bad response).
    Items are dicts shaped per the meetrick-rss-ingest schema:
      {id, source, source_kind, title, url, summary, sm_algo_score,
       raw_score, age_hours, published_at, fetched_at, dedupe_key, ...}
    """
    try:
        params: dict[str, str] = {"limit": str(int(max(1, min(limit, 200))))}
        if source:
            params["source"] = str(source)
        if since_id is not None:
            params["since_id"] = str(int(since_id))
        if min_score is not None:
            params["min_score"] = f"{float(min_score):.4f}"
        qs = urllib.parse.urlencode(params)
        res = _request("GET", f"/api/v1/feed/recent?{qs}")
        if not res:
            return []
        if res.get("ok") is False:
            return []
        items = res.get("items")
        if not isinstance(items, list):
            return []
        # Defensively strip non-dict entries.
        return [it for it in items if isinstance(it, dict)]
    except Exception:
        return []


def mark_processed(item_ids: list[int]) -> bool:
    """Mark items as processed so the next poll skips them.

    Returns True on success, False on any failure (missing secret, network
    error, non-2xx). Caller should still update local last_seen_id even on
    False — the worst case is a small replay window which is idempotent
    on Rick's side via dedupe-by-id.
    """
    try:
        if not item_ids:
            return True
        secret = _secret()
        if not secret:
            return False
        clean = [int(i) for i in item_ids if isinstance(i, (int, str)) and str(i).lstrip("-").isdigit()]
        if not clean:
            return True
        res = _request(
            "POST",
            "/api/v1/feed/mark-processed",
            body={"item_ids": clean},
            headers={"X-Feed-Secret": secret},
        )
        if not res:
            return False
        if res.get("ok") is False:
            return False
        return True
    except Exception:
        return False


__all__ = ["fetch_recent", "mark_processed", "API_BASE"]
