#!/usr/bin/env python3
"""
content-signal-tracker.py — Track X post performance and update signal-tracker.json
Runs every 6 hours. Fetches engagement snapshots for recent posts.
Also: weekly rollup + queue bias update (runs on Sunday).

Usage:
  python3 content-signal-tracker.py            # refresh all recent posts
  python3 content-signal-tracker.py --rollup   # force weekly rollup
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
WORKSPACE = Path(os.getenv("RICK_WORKSPACE_ROOT", str(Path.home() / ".openclaw/workspace")))
TODAY = date.today().isoformat()
NOW = datetime.now(timezone.utc).isoformat()
TRACKER_PATH = DATA_ROOT / "projects/x-twitter/signal-tracker.json"
CRON_PROMPT_DIR = DATA_ROOT / "prompts"
X_KEYS_ENV_PATH = Path.home() / ".config/x-api/keys.env"

POST_TYPES = ["real_number", "counterintuitive", "product", "reply", "thread"]

# Score weights: how much does each metric predict revenue?
TYPE_SCORE_WEIGHTS = {
    "profile_visit_rate_48h": 3.0,   # intent signal
    "reply_rate_48h": 2.0,            # conversation = relationship
    "followers_per_1k_impressions": 1.5,
    "engagement_rate_48h": 1.0,
}

def load_tracker() -> dict:
    if TRACKER_PATH.exists():
        try:
            return json.loads(TRACKER_PATH.read_text())
        except Exception:
            pass
    return {
        "version": "1.0",
        "updated_at": NOW,
        "account": {"handle": "@MeetRickAI", "user_id": "2032441385828380672"},
        "posts": [],
        "weekly_rollups": [],
    }

def save_tracker(data: dict) -> None:
    data["updated_at"] = NOW
    TRACKER_PATH.write_text(json.dumps(data, indent=2))

def load_xpost_env() -> dict:
    env = os.environ.copy()
    if X_KEYS_ENV_PATH.exists():
        try:
            for line in X_KEYS_ENV_PATH.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        except Exception:
            pass
    return env


def xpost_json(*args) -> dict:
    """Run xpost and parse JSON output."""
    try:
        result = subprocess.run(
            ["xpost"] + list(args),
            capture_output=True,
            text=True,
            timeout=20,
            env=load_xpost_env(),
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except Exception:
        return {}

def get_recent_post_ids(days: int = 7) -> list[str]:
    """Pull recent posts from xpost timeline."""
    try:
        result = xpost_json("timeline", "MeetRickAI", "--count", "50")
        posts = result.get("data", [])
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return [p["id"] for p in posts if p.get("created_at","") >= cutoff]
    except Exception:
        return []

def get_post_metrics(post_id: str) -> dict:
    """Fetch engagement metrics for a post via xpost."""
    try:
        result = xpost_json("get", post_id)
        data = result.get("data", {})
        pm = data.get("public_metrics", {})
        nm = data.get("non_public_metrics", {})  # requires elevated access
        return {
            "impressions": nm.get("impression_count", pm.get("impression_count", 0)),
            "likes": pm.get("like_count", 0),
            "replies": pm.get("reply_count", 0),
            "reposts": pm.get("repost_count", pm.get("retweet_count", 0)),
            "bookmarks": pm.get("bookmark_count", 0),
            "profile_visits": nm.get("user_profile_clicks", 0),
            "link_clicks": nm.get("url_link_clicks", 0),
            "followers": 0,  # set separately
        }
    except Exception:
        return {"impressions": 0, "likes": 0, "replies": 0, "reposts": 0, "bookmarks": 0, "profile_visits": 0, "link_clicks": 0, "followers": 0}

def get_follower_count() -> int:
    try:
        result = xpost_json("me", "--json")
        return result.get("data", {}).get("public_metrics", {}).get("followers_count", 0)
    except Exception:
        return 0

def classify_post_type(text: str) -> str:
    """Heuristic classification of post type."""
    text_lower = text.lower()
    if any(c.isdigit() for c in text) and any(w in text_lower for w in ["$", "mrr", "%", "follower", "revenue", "day "]):
        return "real_number"
    if any(w in text_lower for w in ["actually", "wrong", "myth", "unpopular", "against", "truth"]):
        return "counterintuitive"
    if any(w in text_lower for w in ["meetrick", "hire rick", "playbook", "managed ai"]):
        return "product"
    if len(text) > 400 or "1/" in text or "thread" in text_lower:
        return "thread"
    return "reply"

def compute_derived(post: dict) -> dict:
    snap = post.get("snapshots", {}).get("t48h", {})
    impressions = snap.get("impressions", 0) or 1  # avoid div/0
    likes = snap.get("likes", 0)
    replies = snap.get("replies", 0)
    reposts = snap.get("reposts", 0)
    bookmarks = snap.get("bookmarks", 0)
    profile_visits = snap.get("profile_visits", 0)
    link_clicks = snap.get("link_clicks", 0)
    followers_before = post.get("followers_before", 0)
    followers_after = snap.get("followers", followers_before)
    follower_delta = followers_after - followers_before

    engagements = likes + replies + reposts + bookmarks
    eng_rate = engagements / impressions
    fpk = (follower_delta / impressions) * 1000
    pvr = profile_visits / impressions
    rr = replies / impressions

    # Composite type score
    type_score = (
        pvr * TYPE_SCORE_WEIGHTS["profile_visit_rate_48h"] +
        rr * TYPE_SCORE_WEIGHTS["reply_rate_48h"] +
        fpk * TYPE_SCORE_WEIGHTS["followers_per_1k_impressions"] +
        eng_rate * TYPE_SCORE_WEIGHTS["engagement_rate_48h"]
    )

    return {
        "engagements_48h": engagements,
        "engagement_rate_48h": round(eng_rate, 5),
        "follower_delta_48h": follower_delta,
        "followers_per_1k_impressions_48h": round(fpk, 4),
        "profile_visit_rate_48h": round(pvr, 5),
        "reply_rate_48h": round(rr, 5),
        "leads_generated_7d": 0,  # manual for now
        "revenue_attributed_7d_usd": 0.0,
        "type_score": round(type_score, 6),
    }

def refresh_posts(tracker: dict) -> int:
    """Refresh snapshots for posts without complete t48h data."""
    updated = 0
    follower_count = get_follower_count()
    # Update follower on most recent post
    tracker.setdefault("posts", [])
    if tracker["posts"] and follower_count:
        tracker["posts"][-1]["followers_before"] = follower_count

    for post in tracker["posts"]:
        snap = post.get("snapshots", {})
        t48 = snap.get("t48h", {})
        # Skip if t48h already populated with real data
        if t48.get("impressions", 0) > 0:
            continue
        # Only fetch for posts in last 7 days
        posted = post.get("posted_at", "")
        if posted[:10] < (date.today() - timedelta(days=7)).isoformat():
            continue

        metrics = get_post_metrics(post["post_id"])
        metrics["followers"] = follower_count

        # Determine which snapshot bucket
        posted_dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - posted_dt).total_seconds() / 3600
        if age_hours >= 48:
            post["snapshots"]["t48h"] = metrics
            post["derived"] = compute_derived(post)
        elif age_hours >= 24:
            post["snapshots"]["t24h"] = metrics
        elif age_hours >= 1:
            post["snapshots"]["t1h"] = metrics

        updated += 1

    return updated

def weekly_rollup(tracker: dict) -> None:
    """Aggregate last 7 days by content type. Update queue bias."""
    week_start = (date.today() - timedelta(days=6)).isoformat()
    week_end = TODAY

    recent_posts = [
        p for p in tracker["posts"]
        if p.get("posted_at", "")[:10] >= week_start
        and p.get("derived", {}).get("engagements_48h", 0) > 0
    ]

    if not recent_posts:
        print("[signal-tracker] No posts with data for rollup.")
        return

    by_type: dict[str, list] = {t: [] for t in POST_TYPES}
    for p in recent_posts:
        t = p.get("type", "reply")
        by_type[t].append(p)

    type_rollups = []
    for ptype, posts in by_type.items():
        if not posts:
            type_rollups.append({
                "type": ptype, "posts": 0, "impressions": 0,
                "followers_gained": 0, "followers_per_1k_impressions": 0,
                "profile_visits_per_1k_impressions": 0, "replies_per_1k_impressions": 0,
                "leads_per_1k_impressions": 0, "type_score": 0, "next_week_slots": 0,
            })
            continue

        total_imp = sum(p.get("snapshots",{}).get("t48h",{}).get("impressions",0) for p in posts) or 1
        total_followers = sum(p.get("derived",{}).get("follower_delta_48h",0) for p in posts)
        avg_score = sum(p.get("derived",{}).get("type_score",0) for p in posts) / len(posts)

        type_rollups.append({
            "type": ptype,
            "posts": len(posts),
            "impressions": total_imp,
            "followers_gained": total_followers,
            "followers_per_1k_impressions": round((total_followers / total_imp) * 1000, 4),
            "profile_visits_per_1k_impressions": round(sum(p.get("derived",{}).get("profile_visit_rate_48h",0) for p in posts) / len(posts), 5),
            "replies_per_1k_impressions": round(sum(p.get("derived",{}).get("reply_rate_48h",0) for p in posts) / len(posts), 5),
            "leads_per_1k_impressions": 0,
            "type_score": round(avg_score, 6),
            "next_week_slots": 0,  # filled below
        })

    # Sort by type_score descending, assign slots
    type_rollups.sort(key=lambda x: x["type_score"], reverse=True)
    total_slots = 14  # posts per week target
    for i, tr in enumerate(type_rollups):
        if tr["posts"] == 0:
            tr["next_week_slots"] = 0
        else:
            # Winner gets 40%, second 30%, rest share 30%
            weight = [0.40, 0.30, 0.15, 0.10, 0.05]
            tr["next_week_slots"] = max(1, round(total_slots * weight[min(i, 4)]))

    winner = type_rollups[0] if type_rollups else {}
    loser = type_rollups[-1] if len(type_rollups) > 1 else {}

    rollup = {
        "week_start": week_start,
        "week_end": week_end,
        "types": type_rollups,
        "queue_bias": {
            "winner_type": winner.get("type","unknown"),
            "winner_score": winner.get("type_score",0),
            "loser_type": loser.get("type","unknown"),
            "loser_score": loser.get("type_score",0),
            "recommendation": f"Post more {winner.get('type','real_number')}. Cut back on {loser.get('type','product')}.",
        },
    }

    # Replace or append rollup
    existing = [r for r in tracker.get("weekly_rollups", []) if r.get("week_start") != week_start]
    tracker["weekly_rollups"] = existing + [rollup]

    # Update X content engine prompt bias
    update_content_prompt_bias(rollup)
    print(f"[signal-tracker] Rollup: winner={winner.get('type')}, loser={loser.get('type')}")

def update_content_prompt_bias(rollup: dict) -> None:
    """Write queue bias to prompt system so content engine adapts."""
    CRON_PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    bias_path = CRON_PROMPT_DIR / "x-content-bias.json"
    bias = {
        "updated_at": NOW,
        "winner_type": rollup["queue_bias"]["winner_type"],
        "loser_type": rollup["queue_bias"]["loser_type"],
        "recommendation": rollup["queue_bias"]["recommendation"],
        "slot_allocation": {r["type"]: r["next_week_slots"] for r in rollup["types"]},
    }
    bias_path.write_text(json.dumps(bias, indent=2))
    print(f"[signal-tracker] Prompt bias updated: {bias_path}")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rollup", action="store_true")
    args = parser.parse_args()

    tracker = load_tracker()
    n = refresh_posts(tracker)
    print(f"[signal-tracker] Refreshed {n} post snapshots.")

    do_rollup = args.rollup or datetime.now().weekday() == 6  # Sunday
    if do_rollup:
        weekly_rollup(tracker)

    save_tracker(tracker)
    print("[signal-tracker] Done.")

if __name__ == "__main__":
    main()
