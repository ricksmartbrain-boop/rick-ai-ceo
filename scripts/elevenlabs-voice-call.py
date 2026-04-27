#!/usr/bin/env python3
"""
Rick's ElevenLabs Voice Call Sequencer — Day 3 Multi-Touch Trigger

Reads drip subscribers, identifies those who have received 2 email touches
(day_sent >= 2) and are 72h+ from signup with no voice call yet.

For each eligible lead:
  - If phone field exists → fire ElevenLabs outbound call
  - If no phone → log skip gracefully, do not crash sequence

Daily cap: 10 calls max (configurable via --limit).
7-day cooldown per number enforced via calls log.
Business-hours gating: 9am–6pm recipient timezone (defaults to US Eastern).

Logging: ~/rick-vault/operations/elevenlabs-calls.jsonl
Schema: {ts, lead_id, phone, status, duration_s, transcript_url, cost_usd, error}

Usage:
  python3 scripts/elevenlabs-voice-call.py            # live run
  python3 scripts/elevenlabs-voice-call.py --dry-run  # no actual calls
  python3 scripts/elevenlabs-voice-call.py --limit 5  # cap at 5 calls
  python3 scripts/elevenlabs-voice-call.py --smoke    # build payload for test number, print and exit
"""

import json
import os
import sys
import datetime
import time
import uuid
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ENV_FILE = Path.home() / ".openclaw/workspace/config/rick.env"
SUBSCRIBERS_FILE = Path.home() / "rick-vault/projects/email-drip/subscribers.json"
CALLS_LOG = Path.home() / "rick-vault/operations/elevenlabs-calls.jsonl"
CALLS_LOG.parent.mkdir(parents=True, exist_ok=True)

ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"
CALL_ENDPOINT = f"{ELEVEN_API_BASE}/convai/twilio/outbound-call"
CONV_ENDPOINT = f"{ELEVEN_API_BASE}/convai/conversations/{{conv_id}}"

DEFAULT_DAILY_LIMIT = 10
COOLDOWN_DAYS = 7
CALL_WINDOW_START = 9   # 9am recipient tz
CALL_WINDOW_END = 18    # 6pm recipient tz
DEFAULT_TZ = "America/New_York"

# Cost estimate per minute (ElevenLabs Creator tier + Twilio)
COST_PER_MIN_USD = 0.084  # ~$0.07/min ElevenLabs + $0.014/min Twilio


# ---------------------------------------------------------------------------
# Env loader
# ---------------------------------------------------------------------------
def load_env():
    """Load env vars from rick.env if not already set."""
    env_path = Path(ENV_FILE)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val


def require_env(key):
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"❌ {key} not set. Add it to rick.env.", file=sys.stderr)
        sys.exit(1)
    return val


# ---------------------------------------------------------------------------
# Business-hours check
# ---------------------------------------------------------------------------
def is_business_hours(tz_name=DEFAULT_TZ):
    """Return True if current time in tz_name is within call window."""
    try:
        tz = ZoneInfo(tz_name)
        now = datetime.datetime.now(tz)
        return CALL_WINDOW_START <= now.hour < CALL_WINDOW_END
    except Exception:
        return True  # fail-open; caller can override with --force-hours


# ---------------------------------------------------------------------------
# Cooldown enforcement
# ---------------------------------------------------------------------------
def phones_called_recently(cooldown_days=COOLDOWN_DAYS):
    """Return set of phone numbers called within cooldown_days."""
    if not CALLS_LOG.exists():
        return set()
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=cooldown_days)
    called = set()
    with open(CALLS_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = datetime.datetime.fromisoformat(entry.get("ts", "2000-01-01"))
                if ts > cutoff and entry.get("status") not in ("skip_no_phone", "skip_cooldown", "skip_hours", "dry_run_skip"):
                    phone = entry.get("phone", "")
                    if phone:
                        called.add(phone)
            except Exception:
                pass
    return called


def calls_today_count():
    """Return number of real calls initiated today."""
    if not CALLS_LOG.exists():
        return 0
    today = datetime.datetime.utcnow().date().isoformat()
    count = 0
    with open(CALLS_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("ts", "")[:10] == today and entry.get("status") in ("initiated", "completed", "dry_run"):
                    count += 1
            except Exception:
                pass
    return count


# ---------------------------------------------------------------------------
# Log writer
# ---------------------------------------------------------------------------
def log_call(entry: dict):
    with open(CALLS_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ---------------------------------------------------------------------------
# Subscriber loader
# ---------------------------------------------------------------------------
def load_subscribers():
    path = Path(SUBSCRIBERS_FILE)
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def save_subscribers(subs):
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(subs, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# ElevenLabs API call
# ---------------------------------------------------------------------------
def build_call_payload(agent_id, phone_id, to_number, lead_name, lead_email):
    """Build the ElevenLabs outbound call request body."""
    return {
        "agent_id": agent_id,
        "agent_phone_number_id": phone_id,
        "to_number": to_number,
        "conversation_initiation_client_data": {
            "dynamic_variables": {
                "lead_name": lead_name or "there",
                "lead_email": lead_email,
                "touch_count": "2",
                "product_name": "Rick Pro",
                "product_url": "https://meetrick.ai",
                "cta_url": "https://buy.stripe.com/9B69ATaET7vef3S9170x20t",
            }
        }
    }


def fire_call(api_key, agent_id, phone_id, to_number, lead_name, lead_email, dry_run=False):
    """
    Returns dict: {success, conversation_id, call_sid, error}
    """
    payload = build_call_payload(agent_id, phone_id, to_number, lead_name, lead_email)

    if dry_run:
        print(f"  [DRY RUN] Would POST to {CALL_ENDPOINT}")
        print(f"  Payload: {json.dumps(payload, indent=2)}")
        return {"success": True, "conversation_id": f"dry_{uuid.uuid4().hex[:8]}", "call_sid": None, "dry_run": True}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        CALL_ENDPOINT,
        data=data,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            return {
                "success": result.get("success", False),
                "conversation_id": result.get("conversation_id"),
                "call_sid": result.get("callSid"),
                "error": None,
            }
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return {"success": False, "conversation_id": None, "call_sid": None, "error": f"HTTP {e.code}: {body[:200]}"}
    except Exception as ex:
        return {"success": False, "conversation_id": None, "call_sid": None, "error": str(ex)}


def fetch_conversation_details(api_key, conv_id):
    """Fetch duration + transcript URL after call ends. Returns (duration_s, transcript_url)."""
    url = CONV_ENDPOINT.format(conv_id=conv_id)
    req = urllib.request.Request(url, headers={"xi-api-key": api_key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
            duration = data.get("call_duration_secs", 0)
            transcript_url = f"https://elevenlabs.io/app/conversations/{conv_id}"
            return duration, transcript_url
    except Exception:
        return 0, None


# ---------------------------------------------------------------------------
# Main sequencer
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Rick ElevenLabs Day-3 Voice Call Sequencer")
    parser.add_argument("--dry-run", action="store_true", help="Build payloads but do not fire calls")
    parser.add_argument("--smoke", action="store_true", help="Print payload for first eligible lead and exit")
    parser.add_argument("--limit", type=int, default=DEFAULT_DAILY_LIMIT, help="Max calls to place this run")
    parser.add_argument("--force-hours", action="store_true", help="Skip business-hours check")
    args = parser.parse_args()

    load_env()

    api_key = require_env("ELEVENLABS_API_KEY")
    agent_id = require_env("ELEVENLABS_AGENT_ID")
    phone_id = require_env("ELEVENLABS_PHONE_ID")

    now_utc = datetime.datetime.utcnow()
    today_str = now_utc.date().isoformat()

    print(f"=== Rick ElevenLabs Voice Call Sequencer — {now_utc.strftime('%Y-%m-%d %H:%M UTC')} ===")
    if args.dry_run:
        print("  [DRY RUN mode — no actual calls will be placed]")

    # Business-hours gate
    if not args.force_hours and not args.dry_run and not args.smoke:
        if not is_business_hours():
            print("⏰ Outside business hours (9am–6pm ET). Exiting. Use --force-hours to override.")
            sys.exit(0)

    # Daily cap check
    today_count = calls_today_count()
    remaining_cap = args.limit - today_count
    if remaining_cap <= 0 and not args.dry_run and not args.smoke:
        print(f"📵 Daily call cap reached ({args.limit} calls). Done for today.")
        sys.exit(0)

    print(f"📞 Daily cap: {args.limit} | Called today: {today_count} | Remaining: {max(0, remaining_cap)}")

    # Load cooldown set
    cooled_phones = phones_called_recently()

    # Load subscribers
    subscribers = load_subscribers()
    print(f"📋 Subscribers loaded: {len(subscribers)}")

    calls_placed = 0
    skipped_no_phone = 0
    skipped_cooldown = 0
    skipped_not_ready = 0

    for i, sub in enumerate(subscribers):
        if calls_placed >= remaining_cap and not args.smoke:
            break

        email = sub.get("email", "")
        name = sub.get("name", "")
        phone = sub.get("phone", "").strip()
        signup_str = sub.get("signup_date", "")
        day_sent = sub.get("day_sent", 0)
        voice_call_sent = sub.get("voice_call_sent")
        lead_id = sub.get("lead_id") or f"sub_{i}"

        if not email or not signup_str:
            continue

        # Must have sent at least 2 emails (touch 1 + 2) and NOT have a voice call yet
        if day_sent < 2:
            skipped_not_ready += 1
            continue

        if voice_call_sent:
            skipped_not_ready += 1
            continue

        # Must be 72h+ since signup
        try:
            signup_dt = datetime.datetime.fromisoformat(signup_str.replace("Z", "+00:00"))
            if signup_dt.tzinfo is None:
                signup_dt = signup_dt.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            skipped_not_ready += 1
            continue

        hours_elapsed = (now_utc.replace(tzinfo=datetime.timezone.utc) - signup_dt).total_seconds() / 3600
        if hours_elapsed < 72:
            skipped_not_ready += 1
            continue

        ts = now_utc.isoformat() + "Z"

        # --- No phone: skip gracefully ---
        if not phone:
            skipped_no_phone += 1
            log_entry = {
                "ts": ts,
                "lead_id": lead_id,
                "email": email,
                "phone": None,
                "status": "skip_no_phone",
                "duration_s": 0,
                "transcript_url": None,
                "cost_usd": 0,
                "error": "No phone number in subscriber record",
            }
            log_call(log_entry)
            print(f"  ⏭️  {email} — no phone, skipped (logged)")
            # Mark so we don't log skip on every run
            sub["voice_call_sent"] = f"skip_no_phone_{today_str}"
            continue

        # --- Cooldown check ---
        if phone in cooled_phones:
            skipped_cooldown += 1
            print(f"  🔄  {email} ({phone}) — in cooldown, skipped")
            continue

        # --- Smoke mode: just show payload ---
        if args.smoke:
            payload = build_call_payload(agent_id, phone_id, phone, name, email)
            print(f"\n🔬 SMOKE TEST — Payload for {email} ({phone}):")
            print(json.dumps(payload, indent=2))
            print(f"\nWould POST to: {CALL_ENDPOINT}")
            print(f"xi-api-key: {api_key[:12]}...")
            print("\n✅ Payload construction valid. No call fired.")
            return

        # --- Fire call ---
        print(f"  📞 Calling {name or email} at {phone}...")
        result = fire_call(api_key, agent_id, phone_id, phone, name, email, dry_run=args.dry_run)

        cost_est = 0.0
        duration_s = 0
        transcript_url = None

        if result.get("success"):
            calls_placed += 1
            conv_id = result.get("conversation_id", "")
            status = "dry_run" if args.dry_run else "initiated"

            if not args.dry_run:
                # Brief wait then fetch initial status (calls are async)
                time.sleep(3)
                duration_s, transcript_url = fetch_conversation_details(api_key, conv_id)
                cost_est = round((duration_s / 60) * COST_PER_MIN_USD, 4)
                print(f"    ✅ Call initiated — conv_id: {conv_id} | ~{duration_s}s | ~${cost_est}")
            else:
                print(f"    ✅ Dry run OK — conv_id: {conv_id}")

            # Update subscriber
            sub["voice_call_sent"] = ts
            sub["voice_call_conv_id"] = conv_id
        else:
            status = "failed"
            print(f"    ❌ Call failed: {result.get('error', 'unknown')}")

        log_entry = {
            "ts": ts,
            "lead_id": lead_id,
            "email": email,
            "phone": phone,
            "status": status,
            "conversation_id": result.get("conversation_id"),
            "call_sid": result.get("call_sid"),
            "duration_s": duration_s,
            "transcript_url": transcript_url,
            "cost_usd": cost_est,
            "error": result.get("error"),
            "agent_id": agent_id,
        }
        log_call(log_entry)

        # Small delay between calls
        if not args.dry_run and calls_placed < remaining_cap:
            time.sleep(2)

    # Save updated subscriber state
    save_subscribers(subscribers)

    total_eligible = calls_placed + skipped_no_phone + skipped_cooldown
    print(f"\n📊 Run complete:")
    print(f"   Calls placed:       {calls_placed}")
    print(f"   Skipped (no phone): {skipped_no_phone}")
    print(f"   Skipped (cooldown): {skipped_cooldown}")
    print(f"   Not yet ready:      {skipped_not_ready}")

    if calls_placed > 0:
        est_total = round(calls_placed * 2.5 * COST_PER_MIN_USD, 3)  # assume ~2.5min avg
        print(f"\n💰 Est. cost this run: ~${est_total} (assuming 2.5min avg)")
        print(f"   10-call/day cost:  ~${round(10 * 2.5 * COST_PER_MIN_USD, 2)}/day | ~${round(10 * 2.5 * COST_PER_MIN_USD * 30, 0)}/mo")


if __name__ == "__main__":
    main()
