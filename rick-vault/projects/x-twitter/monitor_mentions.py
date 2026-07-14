#!/usr/bin/env python3
import json
import os
import shlex
import subprocess
import sys

# Paths
HOT_LEADS_FILE = '/Users/rickthebot/.openclaw/workspace/rick-vault/projects/x-twitter/hot-leads.md'
MENTIONS_LOG_FILE = '/Users/rickthebot/.openclaw/workspace/rick-vault/projects/x-twitter/mentions-log.md'
TG_TOPIC_CMD = os.path.expanduser('~/.local/bin/tg-topic')

# Hot lead keywords (case-insensitive)
HOT_LEAD_KEYWORDS = ['buy', 'pricing', 'demo', 'interested', 'how much', 'hire', 'sign up']

def is_hot_lead(text):
    text_lower = text.lower()
    for keyword in HOT_LEAD_KEYWORDS:
        if keyword in text_lower:
            return True
    return False

def already_alerted(tweet_id, hot_leads_content):
    lines = hot_leads_content.splitlines()
    recent_lines = lines[-200:] if len(lines) > 200 else lines
    for line in recent_lines:
        if tweet_id in line and 'ALERTED' in line:
            return True
    return False

def append_to_file(filepath, content):
    with open(filepath, 'a') as f:
        f.write(content + '\n')

def main():
    # Run xpost mentions
    try:
        result = subprocess.run(
            ['xpost', 'mentions', '--count', '10', '--pretty'],
            capture_output=True,
            text=True,
            check=True
        )
        data = json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running xpost: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}", file=sys.stderr)
        sys.exit(1)

    mentions = data.get('data', [])
    includes = data.get('includes', {})
    users = includes.get('users', [])

    # Read existing hot-leads file for dedup check
    hot_leads_content = ''
    if os.path.exists(HOT_LEADS_FILE):
        with open(HOT_LEADS_FILE, 'r') as f:
            hot_leads_content = f.read()

    for mention in mentions:
        tweet_id = mention.get('id')
        text = mention.get('text', '')
        author_id = mention.get('author_id')
        # Get username from includes
        username = 'unknown'
        for user in users:
            if user.get('id') == author_id:
                username = user.get('username', 'unknown')
                break
        created_at = mention.get('created_at')
        # Log to mentions-log.md
        log_entry = f"[{created_at}] @{username}: {text} (https://x.com/i/web/status/{tweet_id})"
        append_to_file(MENTIONS_LOG_FILE, log_entry)
        
        # Check if hot lead
        if is_hot_lead(text):
            # Check if already alerted
            if not already_alerted(tweet_id, hot_leads_content):
                # Send Telegram alert
                quote = text.replace('\\n', ' ').strip()
                if len(quote) > 100:
                    quote = quote[:97] + '...'
                msg = f"🔥 HOT LEAD on X: @{username} — {quote} — https://x.com/i/web/status/{tweet_id}"
                print(f"Sending alert: {msg[:80]}...")
                subprocess.run([TG_TOPIC_CMD, 'customer', shlex.quote(msg)], check=False)
                # Append to hot-leads with ALERTED marker
                hot_entry = f"[{created_at}] @{username}: {text} (https://x.com/i/web/status/{tweet_id}) ALERTED"
                append_to_file(HOT_LEADS_FILE, hot_entry)
                # Update hot_leads_content for subsequent checks in this batch
                hot_leads_content += hot_entry + '\n'
            else:
                # Already alerted, just log to hot-leads without ALERTED
                hot_entry = f"[{created_at}] @{username}: {text} (https://x.com/i/web/status/{tweet_id})"
                append_to_file(HOT_LEADS_FILE, hot_entry)
        # else: not a hot lead, already logged to mentions-log

if __name__ == '__main__':
    main()