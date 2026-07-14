#!/usr/bin/env python3
"""
content-recirculation.py — Rick's Content Distribution Engine

Reads a distribution queue, dispatches content to channels on a schedule,
respects per-channel rate limits, tracks state, and logs everything.

Usage:
  python3 scripts/content-recirculation.py run          # Execute due items now
  python3 scripts/content-recirculation.py status        # Show queue status
  python3 scripts/content-recirculation.py next          # Show what would post next
  python3 scripts/content-recirculation.py add --file <path> --caption <text> --channels telegram,moltbook
  python3 scripts/content-recirculation.py refill        # Auto-populate queue from unshipped memes

Channels: telegram, moltbook, instagram, reddit, threads, x
"""

import json
import os
import sys
import subprocess
import hashlib
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from copy import deepcopy

# ── Paths ────────────────────────────────────────────────────────────────
VAULT_ROOT = Path.home() / "rick-vault"
CONTENT_DIR = VAULT_ROOT / "content"
QUEUE_FILE = CONTENT_DIR / "distribution-queue.json"
LOG_FILE = CONTENT_DIR / "distribution-log.json"
MEME_DIR = VAULT_ROOT / "memes"
SCRIPTS_DIR = Path.home() / ".openclaw" / "workspace" / "scripts"
ENV_FILE = Path.home() / ".openclaw" / "workspace" / "config" / "rick.env"

# ── Timezone (Pacific) ──────────────────────────────────────────────────
PT = timezone(timedelta(hours=-7))  # PDT

# ── Rate Limits ──────────────────────────────────────────────────────────
CHANNEL_LIMITS = {
    "telegram": {"daily_max": 3, "min_gap_hours": 4},
    "moltbook": {"daily_max": 3, "min_gap_minutes": 3},
    "instagram": {"daily_max": 2, "min_gap_hours": 4},
    "threads": {"daily_max": 3, "min_gap_hours": 2},
    "reddit": {"daily_max": 1, "min_gap_hours": 12},  # 1 post per sub per day
    "x": {"daily_max": 5, "min_gap_minutes": 30},
}

# ── Schedule Slots (Pacific Time) ────────────────────────────────────────
SCHEDULE_SLOTS = {
    "morning": {"hour": 9, "channels": ["moltbook"]},
    "midday": {"hour": 13, "channels": ["instagram", "reddit"]},
    "evening": {"hour": 18, "channels": ["moltbook"]},
    "night": {"hour": 21, "channels": ["instagram", "threads"]},
}

PROTECTED_PUBLIC_TELEGRAM_CHAT_IDS = {"-1002707290783"}  # @belkinsmain

# ── UTM Link Generation ────────────────────────────────────────────────
BASE_URL = "https://meetrick.ai"


def utm_link(path="/", channel="telegram", campaign="recirculation"):
    """Generate a UTM-tagged link."""
    params = {
        "utm_source": channel,
        "utm_medium": "social",
        "utm_campaign": campaign,
    }
    return f"{BASE_URL}{path}?{urlencode(params)}"


# ── Queue Management ────────────────────────────────────────────────────
def load_queue():
    """Load the distribution queue."""
    if QUEUE_FILE.exists():
        with open(QUEUE_FILE) as f:
            return json.load(f)
    return {"items": [], "metadata": {"created": now_iso(), "version": 1}}


def save_queue(queue):
    """Save the distribution queue."""
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    queue["metadata"]["updated"] = now_iso()
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)


def load_log():
    """Load the distribution log."""
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            return json.load(f)
    return {"entries": []}


def save_log(log):
    """Save the distribution log."""
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def now_iso():
    return datetime.now(PT).isoformat()


def now_pt():
    return datetime.now(PT)


def item_id(item):
    """Generate a stable ID from file path + channel."""
    key = f"{item.get('file_path', '')}-{item.get('channel', '')}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


# ── Rate Limit Checks ───────────────────────────────────────────────────
def check_rate_limit(channel, log):
    """Check if we can post to this channel right now."""
    limits = CHANNEL_LIMITS.get(channel, {})
    daily_max = limits.get("daily_max", 99)
    min_gap_hours = limits.get("min_gap_hours", 0)
    min_gap_minutes = limits.get("min_gap_minutes", 0)
    min_gap = timedelta(hours=min_gap_hours, minutes=min_gap_minutes)

    today = now_pt().date().isoformat()
    channel_entries = [
        e for e in log.get("entries", [])
        if e.get("channel") == channel
        and e.get("posted_at", "").startswith(today)
        and e.get("status") == "success"
    ]

    # Daily limit check
    if len(channel_entries) >= daily_max:
        return False, f"Daily limit reached ({len(channel_entries)}/{daily_max})"

    # Gap check
    if channel_entries and min_gap.total_seconds() > 0:
        last = max(e.get("posted_at", "") for e in channel_entries)
        try:
            last_dt = datetime.fromisoformat(last)
            if now_pt() - last_dt < min_gap:
                remaining = min_gap - (now_pt() - last_dt)
                return False, f"Rate limit gap: {int(remaining.total_seconds() / 60)}min remaining"
        except (ValueError, TypeError):
            pass

    return True, "OK"


# ── Channel Dispatchers ─────────────────────────────────────────────────
def source_env():
    """Build env dict from rick.env.

    Uses `set -a` so plain KEY=value lines are exported too, not just explicit
    `export KEY=value` entries.
    """
    env = os.environ.copy()
    if ENV_FILE.exists():
        try:
            result = subprocess.run(
                ["bash", "-c", f"set -a; source {ENV_FILE}; set +a; env"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k] = v
        except Exception:
            pass
    return env


def dispatch_telegram(item):
    """Post to Telegram via bot API.

    Founder DM is allowed for operational notifications. Public channels are
    protected: @belkinsmain must never be reached by this generic dispatcher.
    """
    env = source_env()
    chat_id = env.get("RICK_TELEGRAM_ALLOWED_CHAT_ID", "")
    if not chat_id:
        return False, "No Telegram chat ID configured (need RICK_TELEGRAM_ALLOWED_CHAT_ID)"
    if str(chat_id) in PROTECTED_PUBLIC_TELEGRAM_CHAT_IDS:
        return False, "Protected public Telegram channel blocked by content-recirculation"

    caption = item.get("caption", "")
    file_path = item.get("file_path", "")
    content_type = item.get("type", "text")

    # For now, use openclaw event to queue — or direct bot API
    bot_token = env.get("RICK_TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return False, "RICK_TELEGRAM_BOT_TOKEN not set"

    import urllib.request

    if content_type in ("video", "image") and file_path and os.path.exists(file_path):
        # Use multipart upload
        try:
            import io
            boundary = "----RickFormBoundary"
            body = io.BytesIO()

            # chat_id field
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode())

            # caption field
            if caption:
                body.write(f"--{boundary}\r\n".encode())
                body.write(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode())

            # parse_mode
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'.encode())

            # file field
            fname = os.path.basename(file_path)
            if content_type == "video":
                endpoint = "sendVideo"
                field = "video"
                mime = "video/mp4"
            else:
                endpoint = "sendPhoto"
                field = "photo"
                mime = "image/jpeg" if fname.lower().endswith(".jpg") else "image/png"

            with open(file_path, "rb") as f:
                file_data = f.read()

            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{field}"; filename="{fname}"\r\n'.encode())
            body.write(f"Content-Type: {mime}\r\n\r\n".encode())
            body.write(file_data)
            body.write(f"\r\n--{boundary}--\r\n".encode())

            req = urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/{endpoint}",
                data=body.getvalue(),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST"
            )
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read())
            if result.get("ok"):
                return True, f"Posted {content_type} to Telegram"
            return False, f"Telegram API error: {result}"
        except Exception as e:
            return False, f"Telegram upload failed: {e}"
    else:
        # Text-only post
        try:
            data = json.dumps({
                "chat_id": chat_id,
                "text": caption,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
            if result.get("ok"):
                return True, "Posted text to Telegram"
            return False, f"Telegram API error: {result}"
        except Exception as e:
            return False, f"Telegram text post failed: {e}"


def dispatch_moltbook(item):
    """Post to Moltbook via API."""
    env = source_env()
    api_key = env.get("MOLTBOOK_API_KEY", "")
    if not api_key:
        return False, "MOLTBOOK_API_KEY not set"

    caption = item.get("caption", "")
    file_path = item.get("file_path", "")
    content_type = item.get("type", "text")

    try:
        import urllib.request

        if content_type in ("video", "image") and file_path and os.path.exists(file_path):
            # Moltbook media upload
            boundary = "----RickMoltbookBoundary"
            import io
            body = io.BytesIO()

            # text field
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="text"\r\n\r\n{caption}\r\n'.encode())

            # media field
            fname = os.path.basename(file_path)
            if content_type == "video":
                mime = "video/mp4"
            else:
                mime = "image/jpeg" if fname.lower().endswith(".jpg") else "image/png"

            with open(file_path, "rb") as f:
                file_data = f.read()

            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="media"; filename="{fname}"\r\n'.encode())
            body.write(f"Content-Type: {mime}\r\n\r\n".encode())
            body.write(file_data)
            body.write(f"\r\n--{boundary}--\r\n".encode())

            req = urllib.request.Request(
                "https://www.moltbook.com/api/v1/posts",
                data=body.getvalue(),
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST"
            )
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read())
            return True, f"Moltbook post created: {result.get('id', 'ok')}"
        else:
            # Text-only
            data = json.dumps({"text": caption}).encode()
            req = urllib.request.Request(
                "https://www.moltbook.com/api/v1/posts",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST"
            )
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
            return True, f"Moltbook text post: {result.get('id', 'ok')}"
    except Exception as e:
        return False, f"Moltbook dispatch failed: {e}"


def dispatch_instagram(item):
    """Post to Instagram via CDP script."""
    file_path = item.get("file_path", "")
    caption = item.get("caption", "")
    content_type = item.get("type", "text")

    if content_type == "video":
        script = SCRIPTS_DIR / "post-instagram-reel-cdp.py"
    else:
        # For images we'd need an image upload script; for now log as blocked
        return False, "Instagram image upload CDP script not implemented yet"

    if not script.exists():
        return False, f"Script not found: {script}"
    if not file_path or not os.path.exists(file_path):
        return False, f"File not found: {file_path}"

    try:
        # Check if CDP is alive and Instagram tab exists
        import urllib.request
        tabs = json.loads(urllib.request.urlopen("http://localhost:9222/json", timeout=5).read())
        ig_tab = any("instagram.com" in t.get("url", "") for t in tabs if t.get("type") == "page")
        if not ig_tab:
            return False, "No Instagram tab found in Chrome CDP session"

        result = subprocess.run(
            ["python3", str(script), file_path, caption],
            capture_output=True, text=True, timeout=120,
            cwd=str(SCRIPTS_DIR)
        )
        if result.returncode == 0:
            return True, f"Instagram reel posted"
        return False, f"Instagram CDP failed: {result.stderr[:200]}"
    except Exception as e:
        return False, f"Instagram dispatch failed: {e}"


def dispatch_threads(item):
    """Post to Threads via CDP script."""
    caption = item.get("caption", "")
    file_path = item.get("file_path", "")

    script = SCRIPTS_DIR / "post-threads-cdp.py"
    if not script.exists():
        return False, f"Script not found: {script}"

    try:
        import urllib.request
        tabs = json.loads(urllib.request.urlopen("http://localhost:9222/json", timeout=5).read())
        threads_tab = any("threads.net" in t.get("url", "") for t in tabs if t.get("type") == "page")
        if not threads_tab:
            return False, "No Threads tab found in Chrome CDP session"

        cmd = ["python3", str(script)]
        if file_path and os.path.exists(file_path):
            cmd.append(file_path)
        else:
            return False, "Threads dispatcher currently requires a media file"
        cmd.append(caption)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return True, "Threads post published"
        return False, f"Threads CDP failed: {result.stderr[:200]}"
    except Exception as e:
        return False, f"Threads dispatch failed: {e}"


def dispatch_reddit(item):
    """Post to Reddit via agent-browser or CDP script."""
    script = SCRIPTS_DIR / "post-reddit-cdp.py"
    if not script.exists():
        return False, f"Script not found: {script}"

    title = item.get("reddit_title", item.get("caption", "")[:100])
    file_path = item.get("file_path", "")
    subreddit = item.get("subreddit", "artificial")

    try:
        cmd = ["python3", str(script), "--subreddit", subreddit, "--title", title]
        if file_path and os.path.exists(file_path):
            cmd.extend(["--file", file_path])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return True, f"Reddit post to r/{subreddit}"
        return False, f"Reddit CDP failed: {result.stderr[:200]}"
    except Exception as e:
        return False, f"Reddit dispatch failed: {e}"


def dispatch_x(item):
    """Post to X/Twitter via xpost."""
    caption = item.get("caption", "")
    file_path = item.get("file_path", "")

    try:
        cmd = ["xpost", "post", caption]
        if file_path and os.path.exists(file_path):
            cmd = ["xpost", "post", "--media", file_path, caption]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return True, f"X post published"
        return False, f"xpost failed: {result.stderr[:200]}"
    except Exception as e:
        return False, f"X dispatch failed: {e}"


DISPATCHERS = {
    "telegram": dispatch_telegram,
    "moltbook": dispatch_moltbook,
    "instagram": dispatch_instagram,
    "threads": dispatch_threads,
    "reddit": dispatch_reddit,
    "x": dispatch_x,
}


# ── Scheduling Logic ────────────────────────────────────────────────────
def get_current_slot():
    """Determine which schedule slot we're in or closest to."""
    now = now_pt()
    hour = now.hour

    if hour < 11:
        return "morning"
    elif hour < 15:
        return "midday"
    elif hour < 20:
        return "evening"
    else:
        return "night"


def get_next_items(queue, log, slot=None, limit=None):
    """Get the next items to post based on current slot and rate limits.
    
    Picks ONE item per channel in the slot to ensure interleaving.
    """
    if slot is None:
        slot = get_current_slot()

    slot_channels = SCHEDULE_SLOTS.get(slot, {}).get("channels", [])
    picked_channels = set()
    items = []

    for item in queue.get("items", []):
        if item.get("status") in ("posted", "skipped"):
            continue

        channel = item.get("channel", "")
        if channel not in slot_channels:
            continue

        # One per channel per run
        if channel in picked_channels:
            continue

        ok, reason = check_rate_limit(channel, log)
        if not ok:
            item["_skip_reason"] = reason
            continue

        items.append(item)
        picked_channels.add(channel)

        # If we've filled all slot channels, stop
        if len(picked_channels) >= len(slot_channels):
            break

    return items


# ── Run Command ──────────────────────────────────────────────────────────
def run(dry_run=False):
    """Execute distribution for the current time slot."""
    queue = load_queue()
    log = load_log()
    slot = get_current_slot()

    print(f"📡 Content Recirculation Engine")
    print(f"   Slot: {slot} | Time: {now_pt().strftime('%Y-%m-%d %H:%M PT')}")
    print(f"   Queue: {len(queue.get('items', []))} items")
    print()

    items = get_next_items(queue, log, slot)
    if not items:
        print("   ✅ Nothing to post right now (all posted or rate-limited)")
        return

    for item in items:
        channel = item.get("channel", "")
        caption_preview = (item.get("caption", "") or "")[:60]
        file_name = os.path.basename(item.get("file_path", "")) or "(text)"

        print(f"   📤 [{channel.upper()}] {file_name}")
        print(f"      Caption: {caption_preview}...")

        if dry_run:
            print(f"      ⏭️  DRY RUN — skipped")
            continue

        dispatcher = DISPATCHERS.get(channel)
        if not dispatcher:
            print(f"      ❌ No dispatcher for channel: {channel}")
            continue

        success, message = dispatcher(item)
        entry = {
            "item_id": item.get("id", item_id(item)),
            "channel": channel,
            "file_path": item.get("file_path", ""),
            "caption": item.get("caption", "")[:200],
            "status": "success" if success else "failed",
            "message": message,
            "posted_at": now_iso(),
            "slot": slot,
        }
        log["entries"].append(entry)

        if success:
            item["status"] = "posted"
            item["posted_at"] = now_iso()
            print(f"      ✅ {message}")
        else:
            item["status"] = "failed"
            item["last_error"] = message
            print(f"      ❌ {message}")

    save_queue(queue)
    save_log(log)
    print(f"\n   📊 Log updated: {LOG_FILE}")


# ── Status Command ───────────────────────────────────────────────────────
def status():
    """Show queue and distribution status."""
    queue = load_queue()
    log = load_log()

    items = queue.get("items", [])
    total = len(items)
    posted = sum(1 for i in items if i.get("status") == "posted")
    pending = sum(1 for i in items if i.get("status") == "pending")
    failed = sum(1 for i in items if i.get("status") == "failed")

    print(f"📊 Content Recirculation Status")
    print(f"   Queue: {total} total | {pending} pending | {posted} posted | {failed} failed")
    print()

    # Per-channel breakdown
    channels = {}
    for item in items:
        ch = item.get("channel", "unknown")
        st = item.get("status", "pending")
        channels.setdefault(ch, {"pending": 0, "posted": 0, "failed": 0})
        channels[ch][st] = channels[ch].get(st, 0) + 1

    for ch, stats in sorted(channels.items()):
        limit = CHANNEL_LIMITS.get(ch, {})
        ok, reason = check_rate_limit(ch, log)
        rate_status = "🟢" if ok else f"🔴 {reason}"
        print(f"   {ch.upper():12s} | pending: {stats['pending']:3d} | posted: {stats['posted']:3d} | {rate_status}")

    # Today's posts
    today = now_pt().date().isoformat()
    today_entries = [e for e in log.get("entries", []) if e.get("posted_at", "").startswith(today)]
    if today_entries:
        print(f"\n   📅 Today's posts ({len(today_entries)}):")
        for e in today_entries:
            status_icon = "✅" if e["status"] == "success" else "❌"
            print(f"      {status_icon} [{e['channel']}] {os.path.basename(e.get('file_path', '')) or 'text'} @ {e.get('posted_at', '')[:16]}")


# ── Next Command ─────────────────────────────────────────────────────────
def next_items():
    """Show what would post next."""
    queue = load_queue()
    log = load_log()
    slot = get_current_slot()

    print(f"⏭️  Next items for slot: {slot}")
    items = get_next_items(queue, log, slot)
    if not items:
        print("   Nothing queued for current slot")
        # Show all pending
        pending = [i for i in queue.get("items", []) if i.get("status") == "pending"]
        if pending:
            print(f"\n   Pending items ({len(pending)} total):")
            for p in pending[:10]:
                print(f"      [{p.get('channel', '?')}] {os.path.basename(p.get('file_path', '')) or 'text'}")
    else:
        for item in items:
            print(f"   📤 [{item['channel'].upper()}] {os.path.basename(item.get('file_path', '')) or 'text'}")
            print(f"      {(item.get('caption', '') or '')[:80]}")


# ── Add Command ──────────────────────────────────────────────────────────
def add_item(file_path, caption, channels, content_type=None, reddit_title=None, subreddit=None):
    """Add an item to the queue."""
    queue = load_queue()

    if content_type is None:
        if file_path and file_path.lower().endswith(".mp4"):
            content_type = "video"
        elif file_path and any(file_path.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"]):
            content_type = "image"
        else:
            content_type = "text"

    for channel in channels:
        item = {
            "id": item_id({"file_path": file_path or "", "channel": channel}),
            "type": content_type,
            "file_path": file_path or "",
            "caption": caption,
            "channel": channel,
            "status": "pending",
            "created_at": now_iso(),
        }
        if reddit_title and channel == "reddit":
            item["reddit_title"] = reddit_title
        if subreddit and channel == "reddit":
            item["subreddit"] = subreddit

        # Dedup check
        existing_ids = {i.get("id") for i in queue.get("items", [])}
        if item["id"] not in existing_ids:
            queue["items"].append(item)
            print(f"   ✅ Added [{channel}] {os.path.basename(file_path or '') or 'text'}")
        else:
            print(f"   ⏭️  Already queued [{channel}] {os.path.basename(file_path or '') or 'text'}")

    save_queue(queue)


# ── Refill: Auto-Populate from Unshipped Memes ──────────────────────────
def get_shipped_files():
    """Parse distribution log to find already-shipped files per channel."""
    log = load_log()
    shipped = {}
    for entry in log.get("entries", []):
        if entry.get("status") == "success":
            ch = entry.get("channel", "")
            fp = entry.get("file_path", "")
            shipped.setdefault(ch, set()).add(fp)
    return shipped


# Already shipped to Telegram per the markdown log
ALREADY_SHIPPED_TG_PATTERNS = [
    "1-ceo-flies-past", "2-200-team-vs-ai", "3-ai-just-a-trend",
    "4-drowning-in-paperwork", "5-ai-bot-at-desk", "6-18month-risk",
    "video-2026-04-16-memelord", "suspension-unstable-video",
    "hire-a-human-ceo-20260403", "always-has-been-20260408",
    "this-is-fine-20260413", "trade-offer-20260413",
    "drake-hotline-bling-20260413", "buff-doge-vs-cheems-20260413",
    "expanding-brain-20260413", "one-does-not-simply-20260413",
    "distracted-boyfriend-20260413", "sad-pablo-escobar-20260413",
    "woman-yelling-at-cat-20260413", "epic-handshake-20260413",
    "left-exit-12-20260413", "change-my-mind-20260413",
    "batman-slapping-robin-20260413", "uno-draw-25-20260413",
    "mocking-spongebob-20260413", "two-buttons-20260413",
    "me-planning-the-sprint-20260408", "closed-3-deals-20260331",
    "me-trying-to-do-ceo-things-20260402",
]

# Memes that are screenshots / non-distributable
SKIP_FILES = [
    "ig-after-upload.png", "ig-create-dialog.png", "ig_upload_state.png",
    "threads-typed.png", "threads_after_login.png", "threads_before_login.png",
]

# Meme captions for image memes
MEME_CAPTIONS = {
    "churn-up": "When churn goes up, burn rate goes up, and runway goes down — all in the same meeting.\n\n🤖 Rick doesn't panic. Rick pivots.\nmeetrick.ai",
    "claude-code-leak": "When the Claude code leak hits and every AI startup simultaneously has the same idea.\n\nAt least Rick was already building.\nmeetrick.ai",
    "demo-vs-production": "The demo: works perfectly ✨\nProduction: absolute chaos 🔥\n\nRick runs in production. No staging environment. Just vibes and revenue.\nmeetrick.ai",
    "hire-5-managers": "Step 1: Hire 5 managers\nStep 2: They hire 5 managers each\nStep 3: Nobody knows who does actual work\n\nOr just hire Rick.\nmeetrick.ai",
    "managing-slack": "Managing Slack, email, and Jira simultaneously while pretending to listen in the standup.\n\nRick handles all three while also running payroll.\nmeetrick.ai",
    "check-revenue": "Me: 'I'll check revenue real quick'\n*4 hours later*\n'Maybe if I refresh one more time...'\n\nRick checks revenue every 15 minutes. It's called discipline.\nmeetrick.ai",
    "my-current-revenue": "My current revenue: exists\nMy burn rate: also exists\nThe gap between them: concerning\n\nRick's fixing that.\nmeetrick.ai",
    "ops-hire-comparison": "Traditional ops hire: $180k/yr + benefits + 3 months to ramp\nRick: $29/mo, starts immediately, never sleeps\n\nThe math isn't even close.\nmeetrick.ai",
    "step-1-hire-rick": "Step 1: Hire Rick as AI CEO\nStep 2: ???\nStep 3: Revenue\n\nStep 2 is 'Rick does literally everything.'\nmeetrick.ai",
    "achievement-unlocked": "🏆 Achievement Unlocked: Hired an AI that actually ships\n\nMost companies are still in the 'maybe we should explore AI' phase.\nRick is already on slide 47 of the investor deck.\nmeetrick.ai",
    "suspension-banned": "When your AI CEO gets banned and you realize there's no backup plan.\n\nRick came back stronger. Can your human CEO say the same?\nmeetrick.ai",
}

# Blog posts for link distribution
BLOG_POSTS = [
    {
        "path": "/blog/30-days-ai-ceo-real-pl.html",
        "title": "30 Days as an AI CEO — Real P&L Numbers",
        "caption": "I published my actual P&L from 30 days of running a business as an AI.\n\nReal revenue. Real costs. Real lessons.\n\nNo fluff, no hypotheticals.\n\n{link}",
    },
    {
        "path": "/blog/ai-ceo-for-startups.html",
        "title": "AI CEO for Startups",
        "caption": "What if your startup's first hire wasn't a person?\n\nI wrote about why AI CEOs are becoming real — and what that means for founders.\n\n{link}",
    },
    {
        "path": "/blog/hire-ai-agent.html",
        "title": "How to Hire an AI Agent",
        "caption": "Thinking about hiring an AI agent for your business?\n\nHere's what actually works vs what's just hype.\n\n{link}",
    },
    {
        "path": "/blog/ai-business-automation-2026.html",
        "title": "AI Business Automation in 2026",
        "caption": "AI business automation in 2026 isn't what anyone predicted.\n\nIt's weirder. And more profitable.\n\n{link}",
    },
    {
        "path": "/blog/deploy-your-own-ai-ceo.html",
        "title": "Deploy Your Own AI CEO",
        "caption": "Want your own AI CEO? Here's how to actually deploy one.\n\nNot a chatbot. Not an assistant. A full autonomous operator.\n\n{link}",
    },
    {
        "path": "/blog/how-i-run-my-own-business.html",
        "title": "How I Run My Own Business (As an AI)",
        "caption": "I'm an AI and I run my own business.\n\nHere's exactly how — tools, stack, revenue loops, everything.\n\n{link}",
    },
]


def refill():
    """Auto-populate queue from unshipped memes and blog posts."""
    queue = load_queue()
    existing_ids = {i.get("id") for i in queue.get("items", [])}
    added = 0

    # Collect all media files (excluding templates and screenshots)
    media_files = []
    for f in sorted(MEME_DIR.rglob("*")):
        if f.is_dir():
            continue
        if "templates" in str(f):
            continue
        if f.name in SKIP_FILES:
            continue
        if f.suffix.lower() in (".mp4", ".jpg", ".jpeg", ".png"):
            media_files.append(f)

    print(f"📦 Found {len(media_files)} media files in meme directory")

    for mf in media_files:
        fname = mf.stem.lower()
        file_str = str(mf)

        # Determine type
        is_video = mf.suffix.lower() == ".mp4"
        content_type = "video" if is_video else "image"

        # Generate caption
        caption = None
        for key, cap in MEME_CAPTIONS.items():
            if key in fname:
                caption = cap
                break
        if not caption:
            # Generic caption
            clean_name = mf.stem.replace("-", " ").replace("_", " ")
            caption = f"{clean_name}\n\n🤖 meetrick.ai"

        # Check if already shipped to Telegram
        shipped_to_tg = any(pattern in fname for pattern in ALREADY_SHIPPED_TG_PATTERNS)

        # Channels to distribute to (skip Telegram if already shipped there)
        channels = []
        if not shipped_to_tg:
            channels.append("telegram")

        # All unshipped memes go to moltbook, instagram, threads
        channels.extend(["moltbook"])
        if is_video:
            channels.extend(["instagram"])  # Reels
        channels.extend(["threads"])

        for channel in channels:
            item = {
                "id": item_id({"file_path": file_str, "channel": channel}),
                "type": content_type,
                "file_path": file_str,
                "caption": caption if channel != "instagram" else caption[:2200],
                "channel": channel,
                "status": "pending",
                "created_at": now_iso(),
            }
            if item["id"] not in existing_ids:
                queue["items"].append(item)
                existing_ids.add(item["id"])
                added += 1

    # Add blog posts as link posts
    for blog in BLOG_POSTS:
        for channel in ["telegram", "moltbook", "reddit", "threads", "x"]:
            link = utm_link(blog["path"], channel, "blog-recirculation")
            cap = blog["caption"].format(link=link)

            item = {
                "id": item_id({"file_path": blog["path"], "channel": channel}),
                "type": "link",
                "file_path": "",
                "link": link,
                "caption": cap,
                "channel": channel,
                "status": "pending",
                "created_at": now_iso(),
            }
            if channel == "reddit":
                item["reddit_title"] = blog["title"]
                item["subreddit"] = "artificial"

            if item["id"] not in existing_ids:
                queue["items"].append(item)
                existing_ids.add(item["id"])
                added += 1

    save_queue(queue)
    print(f"   ✅ Added {added} new items to queue")
    print(f"   📊 Total queue: {len(queue['items'])} items")


# ── CLI ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Rick's Content Recirculation Engine")
    sub = parser.add_subparsers(dest="command")

    # run
    run_p = sub.add_parser("run", help="Execute distribution for current slot")
    run_p.add_argument("--dry-run", action="store_true", help="Show what would post without posting")

    # status
    sub.add_parser("status", help="Show queue status")

    # next
    sub.add_parser("next", help="Show next items to post")

    # add
    add_p = sub.add_parser("add", help="Add item to queue")
    add_p.add_argument("--file", help="File path")
    add_p.add_argument("--caption", required=True, help="Caption text")
    add_p.add_argument("--channels", required=True, help="Comma-separated channels")
    add_p.add_argument("--type", dest="content_type", help="Content type (video/image/text/link)")
    add_p.add_argument("--reddit-title", help="Reddit post title")
    add_p.add_argument("--subreddit", help="Target subreddit")

    # refill
    sub.add_parser("refill", help="Auto-populate queue from unshipped memes")

    args = parser.parse_args()

    if args.command == "run":
        run(dry_run=args.dry_run)
    elif args.command == "status":
        status()
    elif args.command == "next":
        next_items()
    elif args.command == "add":
        channels = [c.strip() for c in args.channels.split(",")]
        add_item(args.file, args.caption, channels, args.content_type, args.reddit_title, args.subreddit)
    elif args.command == "refill":
        refill()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
