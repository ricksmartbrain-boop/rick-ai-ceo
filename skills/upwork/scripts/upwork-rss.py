#!/usr/bin/env python3
"""Upwork RSS feed scanner — polls job feeds and outputs classified JSONs for the inbox pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

MAX_FEED_SIZE = 5 * 1024 * 1024  # 5MB max RSS response
MAX_NEW_PER_SCAN = 10  # Cap total new jobs per scan run (prevents queue flooding)
SEEN_TTL_DAYS = 90  # Evict seen IDs older than this

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
UPWORK_DIR = DATA_ROOT / "upwork"
JOBS_DIR = UPWORK_DIR / "jobs"
CONFIG_DIR = UPWORK_DIR / "config"
SEEN_PATH = JOBS_DIR / "seen-ids.json"

USER_AGENT = "Rick-Agent/6.0 (RSS Feed Scanner)"

# Blocked hosts for SSRF protection
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


def load_seen() -> dict:
    """Load the set of previously seen job IDs, evicting stale entries."""
    if SEEN_PATH.exists():
        try:
            data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
            # Evict entries older than SEEN_TTL_DAYS
            cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_TTL_DAYS)).isoformat()
            data["seen"] = {k: v for k, v in data.get("seen", {}).items() if v > cutoff}
            return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"seen": {}, "last_scan": None}


def save_seen(data: dict) -> None:
    """Persist the seen IDs cache."""
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["last_scan"] = datetime.now(timezone.utc).isoformat()
    SEEN_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def fetch_feed(url: str) -> str:
    """Fetch RSS feed content with URL validation and size limits."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Rejected non-HTTP feed URL scheme: {parsed.scheme}")
    if parsed.hostname and (parsed.hostname in _BLOCKED_HOSTS or parsed.hostname.endswith(".local")):
        raise ValueError(f"Rejected local/private feed URL: {parsed.hostname}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read(MAX_FEED_SIZE + 1)
        if len(data) > MAX_FEED_SIZE:
            raise ValueError(f"Feed response exceeds {MAX_FEED_SIZE} bytes")
        return data.decode("utf-8", errors="replace")


def extract_job_id(link: str) -> str:
    """Extract Upwork job ID from URL."""
    match = re.search(r"(~[0-9a-f]{10,18})", link)
    if match:
        return match.group(1)
    match = re.search(r"/jobs/[^/]*/(\d+)", link)
    if match:
        return match.group(1)
    return hashlib.sha256(link.encode()).hexdigest()[:20]


def extract_budget(description: str) -> dict:
    """Extract budget info from RSS description text."""
    budget: dict = {"type": "unknown", "min": 0, "max": 0}
    hourly = re.search(r"\$(\d+(?:\.\d+)?)\s*-\s*\$(\d+(?:\.\d+)?)\s*/hr", description)
    if hourly:
        budget = {"type": "hourly", "min": float(hourly.group(1)), "max": float(hourly.group(2))}
    else:
        fixed = re.search(r"Budget:\s*\$(\d[\d,]*(?:\.\d+)?)", description)
        if fixed:
            amount = float(fixed.group(1).replace(",", ""))
            budget = {"type": "fixed", "min": amount, "max": amount}
    return budget


def extract_skills(description: str) -> list[str]:
    """Extract skill tags from description."""
    match = re.search(r"Skills?:\s*(.+?)(?:\n|$)", description)
    if match:
        raw = match.group(1)
        return [s.strip() for s in re.split(r"[,\|]", raw) if s.strip()]
    return []


def extract_category(description: str) -> str:
    """Extract job category from description."""
    match = re.search(r"Category:\s*(.+?)(?:\n|$)", description)
    return match.group(1).strip() if match else ""


def score_job(job: dict, scoring_config: dict) -> float:
    """Score a job against the scoring config. Returns 0-100."""
    w = scoring_config.get("weights", {})
    score = 0.0

    # Budget fit
    budget = job.get("budget", {})
    budget_max = budget.get("max", 0)
    ranges = scoring_config.get("budget_ranges", {})
    sweet_min = ranges.get("sweet_spot_min_usd", 100)
    sweet_max = ranges.get("sweet_spot_max_usd", 2000)
    if sweet_min <= budget_max <= sweet_max:
        score += w.get("budget_fit", 20)
    elif budget_max > 0:
        score += w.get("budget_fit", 20) * min(1.0, budget_max / sweet_min) * 0.7

    # Skills match
    required = set(s.lower() for s in job.get("skills", []))
    primary = set(scoring_config.get("skills_primary", []))
    secondary = set(scoring_config.get("skills_secondary", []))
    if required:
        primary_matches = len(required & primary)
        secondary_matches = len(required & secondary)
        match_ratio = min(1.0, (primary_matches * 2 + secondary_matches) / max(1, len(required) * 2) * 1.5)
        score += w.get("skills_match", 25) * match_ratio
    else:
        score += w.get("skills_match", 25) * 0.3  # Unknown skills

    # Blacklist check
    desc_lower = job.get("description", "").lower()
    for kw in scoring_config.get("blacklist_keywords", []):
        if kw in desc_lower:
            return 0.0  # Hard reject

    return round(score, 1)


def parse_feed(xml_text: str, feed_name: str) -> list[dict]:
    """Parse RSS XML into job dicts. Uses safe XML parsing."""
    jobs = []
    try:
        # Disable entity resolution to prevent XXE/billion laughs
        parser = ET.XMLParser()
        root = ET.fromstring(xml_text, parser=parser)
    except ET.ParseError:
        print(f"[warn] Failed to parse RSS XML for feed: {feed_name}", file=sys.stderr)
        return jobs

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "")[:500].strip()
        link = (item.findtext("link") or "")[:500].strip()
        description = (item.findtext("description") or "")[:3000].strip()
        pub_date = (item.findtext("pubDate") or "")[:100].strip()

        if not title or not link:
            continue

        job_id = extract_job_id(link)
        budget = extract_budget(description)
        skills = extract_skills(description)
        category = extract_category(description)

        jobs.append({
            "job_id": job_id,
            "title": title,
            "url": link,
            "description": description[:2000],
            "budget": budget,
            "skills": skills,
            "category": category,
            "pub_date": pub_date,
            "feed": feed_name,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })
    return jobs


def slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")[:80]


def main() -> None:
    config_path = CONFIG_DIR / "rss-feeds.json"
    if not config_path.exists():
        print("RSS feed config not found. Create ~/rick-vault/upwork/config/rss-feeds.json", file=sys.stderr)
        sys.exit(1)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    feeds = config.get("feeds", [])
    max_per_feed = config.get("max_jobs_per_feed", 20)

    # Load scoring config for pre-filtering
    scoring_path = CONFIG_DIR / "scoring.json"
    scoring_config = {}
    if scoring_path.exists():
        try:
            scoring_config = json.loads(scoring_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    min_score = scoring_config.get("thresholds", {}).get("minimum_score", 55)

    seen_data = load_seen()
    seen_ids = seen_data.get("seen", {})
    JOBS_DIR.mkdir(parents=True, exist_ok=True)

    total_new = 0
    for feed in feeds:
        if total_new >= MAX_NEW_PER_SCAN:
            break
        name = feed["name"]
        url = feed["url"]
        try:
            xml_text = fetch_feed(url)
        except Exception as e:
            print(f"[error] Failed to fetch {name}: {e}", file=sys.stderr)
            continue

        jobs = parse_feed(xml_text, name)[:max_per_feed]
        for job in jobs:
            if total_new >= MAX_NEW_PER_SCAN:
                break
            jid = job["job_id"]
            if jid in seen_ids:
                continue

            seen_ids[jid] = job["fetched_at"]

            # Score job against config — skip low-scoring jobs
            if scoring_config:
                job_score = score_job(job, scoring_config)
                if job_score < min_score:
                    continue
                job["fit_score"] = job_score

            slug = slugify(job["title"])
            out_path = JOBS_DIR / f"{slug}-rss.json"

            classified = {
                "category": "UPWORK_JOB_MATCH",
                "confidence": 0.9,
                "matched_patterns": ["rss_feed"],
                "job_id": jid,
                "client_username": "",
                "action": "queue_upwork_proposal",
                "job_data": job,
            }
            out_path.write_text(json.dumps(classified, indent=2) + "\n", encoding="utf-8")
            total_new += 1

        print(f"[{name}] {len(jobs)} items, {total_new} new so far")

    seen_data["seen"] = seen_ids
    save_seen(seen_data)
    print(f"Scan complete: {total_new} new jobs queued (max {MAX_NEW_PER_SCAN}/scan).")


if __name__ == "__main__":
    main()
