#!/usr/bin/env python3
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import requests

RADAR_DIR = Path.home() / "rick-vault" / "brain" / "free-jobs" / "radar"
SEEN_IDS_PATH = RADAR_DIR / "seen_ids.json"
SEARCHES = [
    ("AI CEO", 20),
    ("automate my business", 20),
    ("replace myself AI", 20),
    ("AI founder tools", 20),
]
SCORE_PROMPT = """Score this tweet for buying intent for an AI CEO product ($9-$499/mo, helps founders automate their business with AI):
Tweet: {text}
Author: {username} ({followers} followers)

Return JSON: {{"score": 0-10, "reason": "...", "offer_tier": "$9|$39|$97|$499", "draft_reply": "...", "draft_dm": "..."}}
Score 8+ = hot lead. Keep replies under 200 chars, no em dashes, conversational."""

def load_seen_ids():
    if SEEN_IDS_PATH.exists():
        return set(json.loads(SEEN_IDS_PATH.read_text()))
    return set()

def save_seen_ids(ids):
    RADAR_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_IDS_PATH.write_text(json.dumps(list(ids)))

def run_xpost(args):
    result = subprocess.run(["xpost"] + args, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"[radar] xpost error: {result.stderr.strip()}")
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"[radar] Failed to parse xpost output: {result.stdout[:200]}")
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "tweets" in data:
        return data["tweets"]
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return [data] if data else []

def fetch_tweets():
    all_tweets = []
    for query, count in SEARCHES:
        print(f"[radar] Searching: {query}")
        tweets = run_xpost(["search", query, "--count", str(count)])
        if not tweets:
            tweets = run_xpost(["search", query, "-n", str(count)])
        if not tweets:
            tweets = run_xpost(["search", query])
        all_tweets.extend(tweets)
    print("[radar] Checking @MeetRickAI mentions")
    mentions = run_xpost(["mentions", "--count", "30"])
    if not mentions:
        mentions = run_xpost(["mentions", "-n", "30"])
    if not mentions:
        mentions = run_xpost(["mentions"])
    all_tweets.extend(mentions)
    return all_tweets

def extract_tweet_id(tweet):
    for key in ("id", "tweet_id", "id_str"):
        if key in tweet:
            return str(tweet[key])
    return None

def extract_tweet_fields(tweet):
    text = tweet.get("text") or tweet.get("full_text") or tweet.get("content", "")
    username = (tweet.get("author", {}).get("username") or tweet.get("username") or tweet.get("author_id", "unknown"))
    metrics = tweet.get("public_metrics") or tweet.get("author", {}).get("public_metrics", {})
    followers = metrics.get("followers_count", 0)
    return text, username, followers

def get_openrouter_key():
    return os.environ.get("OPENROUTER_API_KEY", "")

def call_free_model(prompt, system="", model="openai/gpt-oss-120b:free"):
    api_key = get_openrouter_key()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "stream": False},
        timeout=25,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

def parse_json_response(text):
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines[1:] if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                pass
    print(f"[radar] Failed to parse JSON from: {text[:200]}")
    return None

def main():
    print(f"[radar] Starting buyer intent radar — {datetime.now().isoformat()}")
    seen_ids = load_seen_ids()
    print(f"[radar] {len(seen_ids)} previously seen IDs")
    tweets = fetch_tweets()
    print(f"[radar] Fetched {len(tweets)} total tweets")
    new_tweets = []
    for tweet in tweets:
        tid = extract_tweet_id(tweet)
        if tid and tid not in seen_ids:
            new_tweets.append(tweet)
            seen_ids.add(tid)
    print(f"[radar] {len(new_tweets)} new tweets to score")
    if not new_tweets:
        print("[radar] No new tweets found. Done.")
        save_seen_ids(seen_ids)
        return
    leads = []
    for i, tweet in enumerate(new_tweets):
        text, username, followers = extract_tweet_fields(tweet)
        if not text:
            continue
        print(f"[radar] Scoring {i+1}/{len(new_tweets)}: @{username}")
        prompt = SCORE_PROMPT.format(text=text, username=username, followers=followers)
        try:
            response = call_free_model(prompt)
        except Exception as e:
            print(f"[helpers] API error: {e}")
            continue
        result = parse_json_response(response)
        if not result:
            continue
        score = result.get("score", 0)
        if score >= 7:
            result["tweet_id"] = extract_tweet_id(tweet)
            result["tweet_text"] = text
            result["username"] = username
            result["followers"] = followers
            leads.append(result)
            print(f"[radar]   -> Score {score} (hot!)" if score >= 8 else f"[radar]   -> Score {score}")
    save_seen_ids(seen_ids)
    if leads:
        date_str = datetime.now().strftime("%Y-%m-%d")
        leads_path = RADAR_DIR / f"leads-{date_str}.json"
        RADAR_DIR.mkdir(parents=True, exist_ok=True)
        leads_path.write_text(json.dumps(leads, indent=2))
        print(f"[radar] Saved {len(leads)} leads to {leads_path}")
    else:
        print("[radar] No leads scored 7+")
    print("[radar] Done.")

if __name__ == "__main__":
    main()
