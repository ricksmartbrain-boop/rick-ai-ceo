"""Founder graph — IndieHackers + HackerNews + GitHub stitched into one
prospect surface.

Each function returns a list of normalized founder candidates with:
  {
    "platform": "hn" | "github" | "ih",
    "username": str,            # canonical handle on the platform
    "profile_url": str,         # link to follow up
    "display_name": str,
    "bio": str,
    "followers": int | None,
    "evidence": dict,           # source-specific raw context
  }

Stdlib-only. Rate-limited to be polite to public APIs.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any

USER_AGENT = "Rick-FounderGraph/1.0 (+https://meetrick.ai)"


def _http_get_json(url: str, headers: dict | None = None, timeout: int = 12) -> dict | list | None:
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return None


def _http_get_text(url: str, headers: dict | None = None, timeout: int = 12) -> str | None:
    h = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# HackerNews via Algolia (free, no auth, generous rate limit)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_hn_show(limit: int = 30, hours_back: int = 48) -> list[dict[str, Any]]:
    """Pull recent Show HN + Ask HN posts and extract the founder/poster.

    Algolia HN API: https://hn.algolia.com/api
    Each Show HN post is a founder showing their work — high-intent signal.
    """
    cutoff_ts = int(time.time()) - hours_back * 3600
    url = (
        f"https://hn.algolia.com/api/v1/search_by_date"
        f"?tags=show_hn&hitsPerPage={min(limit,100)}"
        f"&numericFilters=created_at_i>{cutoff_ts}"
    )
    payload = _http_get_json(url)
    if not isinstance(payload, dict):
        return []

    out: list[dict[str, Any]] = []
    for hit in payload.get("hits", []) or []:
        author = hit.get("author")
        if not author:
            continue
        story_id = hit.get("objectID")
        out.append({
            "platform": "hn",
            "username": author,
            "profile_url": f"https://news.ycombinator.com/user?id={author}",
            "display_name": author,
            "bio": "",
            "followers": hit.get("points") or 0,
            "evidence": {
                "story_id": story_id,
                "story_url": f"https://news.ycombinator.com/item?id={story_id}",
                "title": hit.get("title", ""),
                "external_url": hit.get("url"),
                "created_at": hit.get("created_at"),
                "points": hit.get("points"),
                "num_comments": hit.get("num_comments"),
            },
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GitHub via public Search API (no auth = 10 req/min, plenty for daily run)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_github_new_founders(limit: int = 30, *, min_followers: int = 10, days_back: int = 30) -> list[dict[str, Any]]:
    """Recent GitHub accounts with founder-shaped traction.

    Heuristic: created in last N days, at least M followers. Still in
    'building publicly' stage = receptive to outreach.
    """
    since = (date.today() - timedelta(days=days_back)).isoformat()
    q = f"created:>{since} followers:>{min_followers}"
    url = (
        f"https://api.github.com/search/users"
        f"?q={urllib.parse.quote(q)}&per_page={min(limit, 100)}&sort=followers&order=desc"
    )
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    payload = _http_get_json(url, headers=headers)
    if not isinstance(payload, dict):
        return []

    out: list[dict[str, Any]] = []
    for item in payload.get("items", []) or []:
        login = item.get("login")
        if not login:
            continue
        out.append({
            "platform": "github",
            "username": login,
            "profile_url": item.get("html_url") or f"https://github.com/{login}",
            "display_name": login,
            "bio": "",
            "followers": None,  # search response doesn't include — would need /users/{login}
            "evidence": {
                "user_id": item.get("id"),
                "avatar_url": item.get("avatar_url"),
                "type": item.get("type"),
            },
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# IndieHackers — HTML scrape of /products page (no public API)
# ─────────────────────────────────────────────────────────────────────────────

# IH product page HTML pattern: maker links look like <a href="/{username}">
# embedded in a known surrounding div. We capture distinct usernames + product
# names via a simple regex, then visit each product page for revenue/traction
# stamps. v1 keeps it simple — surface every found username, score by
# "appears N times on landing page" (= active recently).
_IH_USER_RE = re.compile(r'href="/([a-zA-Z0-9_-]{3,30})"[^>]*>([^<]{2,80})</a>')
_IH_PRODUCT_RE = re.compile(r'href="/product/([a-zA-Z0-9_-]+)"', re.IGNORECASE)


def fetch_indiehackers_products(limit: int = 30) -> list[dict[str, Any]]:
    """Scrape /products and return distinct founder candidates.

    v1 — basic landing-page scrape. v2 follows individual product pages for
    revenue stamps (the hottest IH proof points). v2 deferred to keep the
    blast radius small until we confirm IH's anti-scraping posture.
    """
    html = _http_get_text("https://www.indiehackers.com/products")
    if not html:
        return []

    # Extract distinct (username, display_name) pairs that look like makers
    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    for username, display in _IH_USER_RE.findall(html):
        ulow = username.lower()
        if ulow in seen:
            continue
        # Filter out IH internal routes
        if ulow in {"products", "groups", "podcasts", "interviews", "post", "products?", "about", "newsletters", "search"}:
            continue
        seen.add(ulow)
        found.append((username, display.strip()))
        if len(found) >= limit:
            break

    out: list[dict[str, Any]] = []
    for username, display in found:
        out.append({
            "platform": "ih",
            "username": username,
            "profile_url": f"https://www.indiehackers.com/{username}",
            "display_name": display or username,
            "bio": "",
            "followers": None,
            "evidence": {"source_page": "/products", "scrape_date": date.today().isoformat()},
        })
    return out


def all_sources(*, hn: int = 30, gh: int = 30, ih: int = 30) -> dict[str, list[dict[str, Any]]]:
    """Convenience: pull all three sources back-to-back. Each call is rate-throttled."""
    out: dict[str, list[dict[str, Any]]] = {}
    out["hn"] = fetch_hn_show(limit=hn)
    time.sleep(2.0)
    out["github"] = fetch_github_new_founders(limit=gh)
    time.sleep(2.0)
    out["ih"] = fetch_indiehackers_products(limit=ih)
    return out
