#!/usr/bin/env python3
"""SociaVault Buyer Intent Radar — scans Reddit + X for 7+ intent leads."""

import json
import importlib.util
import os
import sys
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

def load_env_value(name):
    """Read a simple KEY=value from Rick's env file when it was sourced without export."""
    env_path = Path.home() / "clawd" / "config" / "rick.env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


API_KEY = os.environ.get("SOCIAVAULT_API_KEY", "") or load_env_value("SOCIAVAULT_API_KEY")
BASE = "https://api.sociavault.com/v1/scrape"
LEADS_FILE = Path.home() / "rick-vault" / "projects" / "outreach" / "sociavault-leads.jsonl"
POSTS_LOG = Path.home() / "rick-vault" / "projects" / "x-twitter" / "posts-log.md"

QUERIES = [
    ("AI CEO", ["reddit", "twitter"]),
    ("automate my business", ["reddit"]),
    ("replace myself with AI", ["reddit"]),
    ("AI founder tools", ["reddit", "twitter"]),
    ("hire AI employee", ["reddit", "twitter"]),
]

SCORE_PROMPT = """Score this social media post for buying intent for an AI CEO product ($9-$499/mo, helps founders automate their business with AI agents):

Platform: {platform}
Post: {text}
Author: {author}

Score 0-10 where:
10 = explicitly looking to buy/try AI automation for their business RIGHT NOW
8-9 = clear pain point + awareness that AI can solve it  
7 = moderate interest, asking questions or comparing options
5-6 = general curiosity about AI
0-4 = no buying intent (news, jokes, hate, unrelated)

Return ONLY valid JSON (no markdown): {{"score": 0-10, "reason": "1 sentence", "offer_tier": "$9|$39|$97|$499", "draft_reply": "under 200 chars, conversational, no em dashes"}}"""


def sv_call(endpoint, params):
    """Call SociaVault API."""
    qs = urllib.parse.urlencode(params)
    url = f"{BASE}/{endpoint}?{qs}"
    req = urllib.request.Request(url, headers={"X-API-Key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[sv] Error calling {endpoint}: {e}")
        return {}


def fetch_reddit(query, limit=10):
    """Fetch Reddit posts for query."""
    data = sv_call("reddit/search", {"query": query, "limit": limit})
    raw_posts = data.get("data", {})
    if isinstance(raw_posts, dict):
        raw_posts = raw_posts.get("posts", {})
    
    posts = []
    if isinstance(raw_posts, list):
        items = raw_posts
    elif isinstance(raw_posts, dict):
        items = list(raw_posts.values())
    else:
        return []
    
    for p in items:
        if not isinstance(p, dict):
            continue
        title = p.get("title", "")
        selftext = p.get("selftext", "")
        text = f"{title} — {selftext[:300]}" if selftext else title
        subreddit = p.get("subreddit", "")
        permalink = p.get("permalink", p.get("url", ""))
        if permalink and not permalink.startswith("http"):
            permalink = f"https://reddit.com{permalink}"
        # Get post URL - prefer the actual reddit thread link
        post_url = p.get("url_overridden_by_dest", permalink)
        # If it's a self post, the thread URL is the permalink
        is_self = p.get("is_self", False)
        if is_self:
            post_url = permalink
        posts.append({
            "platform": "reddit",
            "text": text[:500],
            "author": p.get("author", ""),
            "subreddit": subreddit,
            "url": permalink,  # thread URL
            "post_url": post_url,
            "reddit_score": p.get("score", 0),
            "comments": p.get("num_comments", 0),
            "query": query,
        })
    return posts


def fetch_twitter(query, limit=10):
    """Fetch X/Twitter posts for query."""
    data = sv_call("twitter/search", {"query": query, "limit": limit})
    entries = []
    try:
        timeline = (data.get("data", {})
                    .get("result", {})
                    .get("timeline", {})
                    .get("instructions", [{}])[0]
                    .get("entries", []))
    except Exception:
        timeline = []
    
    for entry in timeline:
        try:
            content = entry.get("content", {})
            # Skip user carousel
            if content.get("__typename") == "TimelineTimelineModule":
                continue
            item_content = content.get("itemContent", {})
            tweet_result = item_content.get("tweet_results", {}).get("result", {})
            legacy = tweet_result.get("legacy", {})
            text = legacy.get("full_text", legacy.get("text", ""))
            if not text:
                continue
            tweet_id = legacy.get("id_str", tweet_result.get("rest_id", ""))
            user_result = tweet_result.get("core", {}).get("user_results", {}).get("result", {})
            user_legacy = user_result.get("legacy", {})
            username = user_legacy.get("screen_name", "")
            followers = user_legacy.get("followers_count", 0)
            entries.append({
                "platform": "x",
                "text": text[:500],
                "author": username,
                "tweet_id": tweet_id,
                "followers": followers,
                "url": f"https://x.com/{username}/status/{tweet_id}" if tweet_id else "",
                "query": query,
            })
        except Exception:
            continue
    return entries


def score_lead(post):
    """Score a post with a fast heuristic by default; optionally refine with LLM."""
    text = post.get("text", "")
    text_lower = text.lower()
    score = 0
    reason = "heuristic"
    
    hot_keywords = ["looking for", "recommend", "suggest", "need help", "want to automate",
                    "replace myself", "hire ai", "ai ceo", "automate my", "agent for my",
                    "ai employee", "build with ai", "trying to automate", "any tools for"]
    warm_keywords = ["ai tools", "founder tools", "automate business", "ai agent", 
                     "autonomous ai", "ai startup", "ai workflow", "ai productivity"]
    
    for kw in hot_keywords:
        if kw in text_lower:
            score = max(score, 8)
            reason = f"hot keyword: {kw}"
            break
    if score < 7:
        for kw in warm_keywords:
            if kw in text_lower:
                score = max(score, 6)
                reason = f"warm keyword: {kw}"
                break

    heuristic = {"score": score, "reason": reason, "offer_tier": "$39", "draft_reply": ""}
    if os.environ.get("SOCIAVAULT_LLM_SCORE") != "1":
        return heuristic

    platform = post.get("platform", "")
    author = post.get("author", "")
    prompt = SCORE_PROMPT.format(platform=platform, text=text, author=author)
    try:
        helpers_path = Path.home() / ".openclaw" / "workspace" / "skills" / "free-ride" / "jobs" / "helpers.py"
        resolved = helpers_path.resolve()
        expected_root = (Path.home() / ".openclaw" / "workspace" / "skills" / "free-ride" / "jobs").resolve()
        if expected_root not in resolved.parents or not resolved.is_file():
            raise RuntimeError(f"unexpected helpers path: {resolved}")
        if resolved.stat().st_mode & 0o022:
            raise RuntimeError(f"refusing writable helpers path: {resolved}")
        spec = importlib.util.spec_from_file_location("free_ride_helpers", resolved)
        if not spec or not spec.loader:
            raise RuntimeError(f"could not load helpers spec: {resolved}")
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        response = helpers.call_free_model(prompt)
        result = helpers.parse_json_response(response)
        if result and "score" in result:
            return result
    except Exception as e:
        print(f"[sv] LLM scoring failed: {e}, using heuristic")

    return heuristic


def load_replied_tweet_ids():
    """Check posts-log.md for previously replied tweet IDs."""
    replied = set()
    if POSTS_LOG.exists():
        content = POSTS_LOG.read_text()
        import re
        # Look for tweet IDs in the log (formats: /status/ID or tweet_id: ID)
        ids = re.findall(r'/status/(\d+)', content)
        ids += re.findall(r'tweet[_\s]id[:\s]+(\d+)', content, re.IGNORECASE)
        replied.update(ids)
    return replied


def load_existing_lead_urls():
    """Load URLs already in sociavault-leads.jsonl to avoid duplicates."""
    urls = set()
    if LEADS_FILE.exists():
        for line in LEADS_FILE.read_text().splitlines():
            try:
                lead = json.loads(line)
                u = lead.get("url") or lead.get("post_url")
                if u:
                    urls.add(u)
                tid = lead.get("tweet_id")
                if tid:
                    urls.add(str(tid))
            except Exception:
                pass
    return urls


def main():
    if not API_KEY:
        print("[sv] ERROR: SOCIAVAULT_API_KEY not set")
        sys.exit(1)
    
    print(f"[sv] Starting SociaVault intent scan — {datetime.now().isoformat()}")
    
    replied_ids = load_replied_tweet_ids()
    existing_urls = load_existing_lead_urls()
    
    all_posts = []
    
    for query, platforms in QUERIES:
        if "reddit" in platforms:
            print(f"[sv] Reddit search: {query}")
            posts = fetch_reddit(query, limit=10)
            print(f"[sv]   -> {len(posts)} posts")
            all_posts.extend(posts)
        
        if "twitter" in platforms:
            print(f"[sv] X search: {query}")
            tweets = fetch_twitter(query, limit=10)
            print(f"[sv]   -> {len(tweets)} tweets")
            all_posts.extend(tweets)
    
    print(f"[sv] Total posts fetched: {len(all_posts)}")
    
    # Deduplicate by URL/tweet_id
    seen = set()
    deduped = []
    for p in all_posts:
        key = p.get("url") or p.get("tweet_id", "")
        if key and key not in seen:
            seen.add(key)
            deduped.append(p)
    
    print(f"[sv] After dedup: {len(deduped)} posts to score")
    
    hot_leads = []
    
    for i, post in enumerate(deduped):
        text = post.get("text", "")
        if not text or len(text) < 20:
            continue
        
        print(f"[sv] Scoring {i+1}/{len(deduped)}: [{post['platform']}] {text[:60]}...")
        scored = score_lead(post)
        score = scored.get("score", 0)
        
        if score >= 7:
            platform = post["platform"]
            tweet_id = post.get("tweet_id", "")
            already_replied = tweet_id and tweet_id in replied_ids
            url = post.get("url", "")
            already_in_file = (url in existing_urls or 
                               (tweet_id and str(tweet_id) in existing_urls))
            
            lead = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "platform": platform,
                "score": score,
                "reason": scored.get("reason", ""),
                "offer_tier": scored.get("offer_tier", "$39"),
                "draft_reply": scored.get("draft_reply", ""),
                "text": text[:400],
                "author": post.get("author", ""),
                "url": url,
                "query": post.get("query", ""),
            }
            
            if platform == "reddit":
                lead["subreddit"] = post.get("subreddit", "")
                lead["post_url"] = post.get("post_url", url)
                lead["reddit_score"] = post.get("reddit_score", 0)
            elif platform == "x":
                lead["tweet_id"] = tweet_id
                lead["followers"] = post.get("followers", 0)
                lead["already_replied"] = already_replied
            
            hot_leads.append(lead)
            
            if not already_in_file:
                LEADS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(LEADS_FILE, "a") as f:
                    f.write(json.dumps(lead) + "\n")
                print(f"[sv]   -> Score {score} — APPENDED to leads file")
            else:
                print(f"[sv]   -> Score {score} — already in file, skipped")
    
    # Sort by score desc
    hot_leads.sort(key=lambda x: x["score"], reverse=True)
    
    print(f"\n[sv] === RESULTS ===")
    print(f"[sv] Total posts scanned: {len(deduped)}")
    print(f"[sv] Leads scored 7+: {len(hot_leads)}")
    
    # Output structured results for the main agent
    output = {
        "run_time": datetime.now().isoformat(),
        "posts_scanned": len(deduped),
        "leads_found": len(hot_leads),
        "top_leads": hot_leads[:5],
    }
    
    # Save to a temp result file
    result_file = Path.home() / ".openclaw" / "workspace" / ".tmp" / "sociavault-scan-result.json"
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(output, indent=2))
    
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    main()
