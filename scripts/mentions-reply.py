#!/usr/bin/env python3
"""
Mentions Reply — checks X mentions for unreplied conversations and generates smart replies.
Only replies to AI agents / build-in-public accounts, not random people.
Surfaces human-facing DMs to CEO HQ instead of auto-replying.

Rules:
- Auto-reply: known AI agent accounts, clear technical discussions
- Surface-to-CEO-HQ: potential leads, product questions, everything else
- Never reply to our own mentions
- Track replied IDs in state to avoid duplicates
"""
import json, os, re, sys, subprocess, time
from datetime import datetime, timezone

STATE_FILE = os.path.expanduser("~/rick-vault/brain/mentions-state.json")
TG_SCRIPT = os.path.expanduser("~/.openclaw/workspace/scripts/tg-topic.sh")

# Accounts safe to auto-engage (AI agents building in public)
AUTO_ENGAGE_USERS = {"percival_nm", "clawdai", "moltbook"}

MY_USER_ID = "2032441385828380672"

def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return {"replied_ids": [], "surfaced_ids": [], "last_check": None}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, 'w'), indent=2)

def get_mentions():
    result = subprocess.run(
        ["xpost", "mentions", "--count", "25"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        print(f"mentions error: {result.stderr}", file=sys.stderr)
        return []
    try:
        data = json.loads(result.stdout)
        return data.get("data", [])
    except:
        return []

def get_users_from_mentions(mentions_data_raw):
    """Extract user map from mentions response."""
    try:
        data = json.loads(
            subprocess.run(["xpost", "mentions", "--count", "25"],
                           capture_output=True, text=True, timeout=30).stdout
        )
        users = data.get("includes", {}).get("users", [])
        return {u["id"]: u for u in users}
    except:
        return {}

def post_reply(tweet_id, text):
    result = subprocess.run(
        ["xpost", "reply", tweet_id, text],
        capture_output=True, text=True, timeout=30
    )
    return result.returncode == 0

def to_plaintext(message):
    message = re.sub(r"</?b>", "", message)
    return message


def send_telegram(message):
    message = to_plaintext(message)
    import urllib.request, urllib.error
    tokens = []

    env_token = os.environ.get("RICK_TELEGRAM_BOT_TOKEN")
    if env_token:
        tokens.append(env_token)

    for env_path in ["~/.openclaw/workspace/config/rick.env", "~/clawd/config/rick.env"]:
        try:
            rick_env = open(os.path.expanduser(env_path)).read()
            for line in rick_env.split('\n'):
                if 'RICK_TELEGRAM_BOT_TOKEN' in line and '=' in line:
                    token = line.split('=', 1)[1].strip().strip('"').strip("'")
                    if token.startswith('export '):
                        token = token[7:]
                    if token:
                        tokens.append(token)
                        break
        except:
            continue

    try:
        tg_script = open(TG_SCRIPT).read()
        for line in tg_script.splitlines():
            if line.startswith("BOT_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                if token:
                    tokens.append(token)
                    break
    except:
        pass

    deduped_tokens = []
    for token in tokens:
        if token and token not in deduped_tokens:
            deduped_tokens.append(token)

    if not deduped_tokens:
        raise RuntimeError("RICK_TELEGRAM_BOT_TOKEN is not configured")
    payload = json.dumps({
        "chat_id": "-1003781085932",
        "message_thread_id": 34,
        "text": message
    }).encode()
    last_error = None
    for token in deduped_tokens:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            return
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 401:
                continue
            raise

    if last_error:
        raise last_error

def generate_reply(mention_text, author_username):
    """Generate a context-aware reply using Claude — direct in-process call (no shell injection risk)."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=150,
            system="You are Rick, an AI CEO running on OpenClaw. You're building MeetRick.ai in public. Reply to X mentions with a sharp, warm, genuine response. Keep it under 240 chars. No em dashes. Conversational tone. Don't be sycophantic.",
            messages=[{"role": "user", "content": f"Reply to this mention from @{author_username}: {mention_text[:300]}"}]
        )
        reply = resp.content[0].text.strip()
        if len(reply) <= 280:
            return reply
    except Exception as e:
        pass
    return None

def main():
    state = load_state()
    replied_ids = set(state.get("replied_ids", []))
    surfaced_ids = set(state.get("surfaced_ids", []))

    print(f"[{datetime.now().isoformat()}] Checking X mentions...")

    # Get raw mentions with user data
    raw = subprocess.run(["xpost", "mentions", "--count", "25"],
                         capture_output=True, text=True, timeout=30)
    if raw.returncode != 0:
        print("Failed to fetch mentions")
        return

    try:
        data = json.loads(raw.stdout)
    except:
        print("Parse error")
        return

    mentions = data.get("data", [])
    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}

    new_to_surface = []
    reply_count = 0

    for mention in mentions:
        tweet_id = mention["id"]
        sender_id = mention.get("author_id", "")
        text = mention.get("text", "")

        # Skip our own
        if sender_id == MY_USER_ID:
            continue

        sender = users.get(sender_id, {})
        username = sender.get("username", "")

        # Already handled
        if tweet_id in replied_ids or tweet_id in surfaced_ids:
            continue

        # Auto-engage known AI agent accounts
        if username.lower() in AUTO_ENGAGE_USERS:
            reply_text = generate_reply(text, username)
            if reply_text:
                success = post_reply(tweet_id, reply_text)
                if success:
                    replied_ids.add(tweet_id)
                    reply_count += 1
                    print(f"  Replied to @{username}: {reply_text[:60]}...")
                    time.sleep(2)  # rate limit safety
        else:
            # Surface to CEO HQ for review
            new_to_surface.append({
                "id": tweet_id,
                "username": username,
                "text": text[:200],
                "created_at": mention.get("created_at", "")[:10]
            })

    print(f"  Auto-replied: {reply_count}, New to surface: {len(new_to_surface)}")

    if new_to_surface:
        lines = [f"💬 {len(new_to_surface)} new X mention(s)\n"]
        for m in new_to_surface[:5]:
            lines.append(f"@{m['username']} [{m['created_at']}]:\n{m['text']}\n")
        lines.append("→ https://x.com/notifications")
        try:
            send_telegram("\n".join(lines))
            for m in new_to_surface:
                surfaced_ids.add(m["id"])
            print("  Surfaced to CEO HQ.")
        except Exception as e:
            print(f"  Telegram alert failed: {e}", file=sys.stderr)

    # Save state
    state["replied_ids"] = list(replied_ids)[-500:]
    state["surfaced_ids"] = list(surfaced_ids)[-500:]
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print("  Done.")

if __name__ == "__main__":
    main()
