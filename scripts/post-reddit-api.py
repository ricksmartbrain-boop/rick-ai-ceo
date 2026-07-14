#!/usr/bin/env python3
"""Post to Reddit via OAuth2 API (password grant flow)."""
import argparse
import os
import sys

import requests

ENV_FILE = os.path.expanduser("~/.openclaw/workspace/config/rick.env")
REQUIRED_VARS = [
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USERNAME",
    "REDDIT_PASSWORD",
]
USER_AGENT = "rick-ai-ceo:v1.0 (by /u/rick-ai-bot)"


def load_env_file():
    """Load env vars from rick.env if they aren't already set."""
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val


def get_credentials():
    load_env_file()
    creds = {}
    missing = []
    for var in REQUIRED_VARS:
        val = os.environ.get(var, "").strip()
        if not val:
            missing.append(var)
        else:
            creds[var] = val
    return creds, missing


def authenticate(creds):
    """Reddit OAuth2 password flow. Returns access token or None."""
    auth = requests.auth.HTTPBasicAuth(
        creds["REDDIT_CLIENT_ID"], creds["REDDIT_CLIENT_SECRET"]
    )
    data = {
        "grant_type": "password",
        "username": creds["REDDIT_USERNAME"],
        "password": creds["REDDIT_PASSWORD"],
    }
    resp = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=auth,
        data=data,
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"AUTH ERROR: HTTP {resp.status_code} — {resp.text[:200]}")
        return None
    body = resp.json()
    token = body.get("access_token")
    if not token:
        print(f"AUTH ERROR: No token in response — {body}")
        return None
    print(f"Authenticated as u/{creds['REDDIT_USERNAME']}")
    return token


def submit_text_post(token, subreddit, title, body_text):
    """Submit a text (self) post to a subreddit."""
    headers = {
        "Authorization": f"bearer {token}",
        "User-Agent": USER_AGENT,
    }
    data = {
        "api_type": "json",
        "kind": "self",
        "sr": subreddit,
        "title": title,
        "text": body_text or "",
    }
    resp = requests.post(
        "https://oauth.reddit.com/api/submit",
        headers=headers,
        data=data,
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"POST ERROR: HTTP {resp.status_code} — {resp.text[:300]}")
        return False
    result = resp.json()
    errors = result.get("json", {}).get("errors", [])
    if errors:
        print(f"POST ERROR: {errors}")
        return False
    post_url = result.get("json", {}).get("data", {}).get("url", "")
    print(f"POSTED: {post_url}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Post to Reddit via API")
    parser.add_argument("--title", required=True, help="Post title")
    parser.add_argument("--subreddit", required=True, help="Subreddit name (without r/)")
    parser.add_argument("--body", default="", help="Post body text")
    parser.add_argument("--dry-run", action="store_true", help="Authenticate but don't post")
    args = parser.parse_args()

    creds, missing = get_credentials()
    if missing:
        print("ERROR: Missing Reddit API credentials.")
        print(f"  Missing: {', '.join(missing)}")
        print()
        print("Setup instructions:")
        print("  1. Visit https://www.reddit.com/prefs/apps")
        print("  2. Click 'create another app...' → select 'script'")
        print("  3. Set redirect URI to http://localhost:8080")
        print("  4. Copy the client ID (under app name) and secret")
        print(f"  5. Add to {ENV_FILE}:")
        print()
        for var in REQUIRED_VARS:
            print(f"     {var}=your_value_here")
        print()
        sys.exit(1)

    token = authenticate(creds)
    if not token:
        sys.exit(1)

    if args.dry_run:
        print(f"[DRY RUN] Would post to r/{args.subreddit}: {args.title[:80]}")
        return

    ok = submit_text_post(token, args.subreddit, args.title, args.body)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
