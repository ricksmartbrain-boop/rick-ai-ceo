#!/usr/bin/env python3
"""
DM Monitor — checks X DMs and surfaces unread/new messages to Telegram CEO HQ.
Tracks seen IDs in state file to avoid re-alerting.
"""
import json, os, sys, time, hmac, hashlib, base64, uuid
import urllib.request, urllib.parse
from datetime import datetime, timezone

# Paths
KEYS_FILE = os.path.expanduser("~/.config/x-api/keys.env")
STATE_FILE = os.path.expanduser("~/rick-vault/brain/dm-state.json")
TG_SCRIPT = os.path.expanduser("~/.openclaw/workspace/scripts/tg-topic.sh")

# Telegram target: CEO HQ topic
TG_CHAT_ID = "-1003781085932"
TG_THREAD_ID = "34"

def load_keys():
    env = {}
    for line in open(KEYS_FILE).read().strip().split('\n'):
        if '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    return env

def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return {"x_seen_ids": [], "last_check": None}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, 'w'), indent=2)

def oauth1_get(url, params, keys):
    oauth_params = {
        "oauth_consumer_key": keys["X_API_KEY"],
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": keys["X_ACCESS_TOKEN"],
        "oauth_version": "1.0"
    }
    all_params = {**params, **oauth_params}
    param_str = "&".join(
        f"{urllib.parse.quote(k, '')}={urllib.parse.quote(v, '')}"
        for k, v in sorted(all_params.items())
    )
    base = f"GET&{urllib.parse.quote(url, '')}&{urllib.parse.quote(param_str, '')}"
    signing_key = f"{urllib.parse.quote(keys['X_API_SECRET'], '')}&{urllib.parse.quote(keys['X_ACCESS_TOKEN_SECRET'], '')}"
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    oauth_params["oauth_signature"] = sig
    auth_header = "OAuth " + ", ".join(
        f'{k}="{urllib.parse.quote(v)}"' for k, v in sorted(oauth_params.items())
    )
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers={"Authorization": auth_header})
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())

def get_x_dms(keys):
    """Fetch recent DM events from X API v2."""
    url = "https://api.twitter.com/2/dm_events"
    params = {
        "max_results": "50",
        "dm_event.fields": "text,sender_id,created_at,dm_conversation_id",
        "expansions": "sender_id",
        "user.fields": "name,username"
    }
    try:
        data = oauth1_get(url, params, keys)
        return data
    except Exception as e:
        print(f"X DM fetch error: {e}", file=sys.stderr)
        return None

def send_telegram_alert(message):
    """Send alert via openclaw message send (tg-topic.sh fallback)."""
    import subprocess
    # Primary: openclaw message send → ops-alerts (chat -1003781085932, tid 34)
    try:
        r = subprocess.run(
            [
                "openclaw", "message", "send",
                "--channel", "telegram",
                "--target", TG_CHAT_ID,
                "--thread-id", TG_THREAD_ID,
                "--message", message,
            ],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0:
            return
    except Exception:
        pass
    # Fallback: tg-topic.sh
    result = subprocess.run(
        ["bash", TG_SCRIPT, "ops-alerts", message],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        # Fallback: direct telegram bot API
        token = None
        for env_path in ["~/.openclaw/workspace/config/rick.env", "~/clawd/config/rick.env"]:
            try:
                rick_env = open(os.path.expanduser(env_path)).read()
                for line in rick_env.split('\n'):
                    if 'RICK_TELEGRAM_BOT_TOKEN' in line and '=' in line:
                        token = line.split('=', 1)[1].strip().strip('"').strip("'").replace('export ', '')
                        if token:
                            break
                if token:
                    break
            except:
                continue
        if token:
            tg_url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({
                "chat_id": TG_CHAT_ID,
                "message_thread_id": int(TG_THREAD_ID),
                "text": message,
                "parse_mode": "HTML"
            }).encode()
            req = urllib.request.Request(tg_url, data=payload,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)

def format_x_dm_alert(events, users_by_id, my_user_id, new_count):
    """Format DM alert message."""
    lines = [f"📬 <b>{new_count} new X DM(s)</b>\n"]
    for event in events[:5]:  # max 5 in alert
        sender_id = event.get("sender_id", "")
        if sender_id == my_user_id:
            continue  # skip our own outbound
        sender = users_by_id.get(sender_id, {})
        name = sender.get("name", "Unknown")
        username = sender.get("username", sender_id)
        text = event.get("text", "")[:200]
        created = event.get("created_at", "")[:10]
        lines.append(f"<b>@{username}</b> ({name}) [{created}]:\n{text}\n")
    lines.append("\n→ https://x.com/messages")
    return "\n".join(lines)

def main():
    keys = load_keys()
    state = load_state()
    seen_ids = set(state.get("x_seen_ids", []))
    my_user_id = keys.get("X_USER_ID", "2032441385828380672")

    print(f"[{datetime.now().isoformat()}] Checking X DMs...")

    # Fetch X DMs
    dm_data = get_x_dms(keys)
    if not dm_data or "data" not in dm_data:
        print("No DM data returned")
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        return

    events = dm_data.get("data", [])
    users = {u["id"]: u for u in dm_data.get("includes", {}).get("users", [])}

    # Find new inbound DMs (not from us, not seen before)
    new_events = []
    for event in events:
        if event["id"] not in seen_ids and event.get("sender_id") != my_user_id:
            new_events.append(event)

    print(f"  Total events: {len(events)}, New inbound: {len(new_events)}")

    if new_events:
        alert = format_x_dm_alert(new_events, users, my_user_id, len(new_events))
        print(f"  Sending alert: {len(new_events)} new DMs")
        try:
            send_telegram_alert(alert)
            print("  Alert sent.")
        except Exception as e:
            print(f"  Alert failed: {e}", file=sys.stderr)

    # Update seen IDs (keep last 500 to avoid unbounded growth)
    all_ids = [e["id"] for e in events]
    state["x_seen_ids"] = list(set(seen_ids) | set(all_ids))[-500:]
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    state["last_new_count"] = len(new_events)
    save_state(state)
    print(f"  State saved. Done.")

if __name__ == "__main__":
    main()
