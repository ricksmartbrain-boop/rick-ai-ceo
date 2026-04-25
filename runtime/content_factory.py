"""Content factory — produces public posts on a cron, queues via outbound_dispatcher.

Was scaffolded in master plan Phase E but never written → outbound_jobs table
stayed empty → moltbook/threads/instagram silent for 4+ days. This MVP fills
the gap for TEXT channels (moltbook). Threads + instagram need video/image
paths the formatters expect; out of scope without an image-gen pipeline.

Each tick:
  1. Picks a content angle from a small rotation (case study / metric brag /
     feature-of-the-day / pure-shitpost)
  2. Generates a short post via writing route (sonnet-4-6 workhorse)
  3. Queues via outbound_dispatcher.fan_out — Fenix preflight gates publication
     based on customer-naming/pricing/legal triggers
  4. The 5-min outbound dispatcher cron picks it up and ships through formatter

Override: RICK_CONTENT_FACTORY_DISABLED=1 to silence (e.g., during deploys).

Usage:
    python3 -m runtime.content_factory --targets 1
    python3 -m runtime.content_factory --targets 3 --channels moltbook,threads
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LOG_FILE = DATA_ROOT / "operations" / "content-factory.jsonl"

# Angles are short prompt-style hooks. Real prompt is built per-channel in _build_prompt.
ANGLES = [
    ("operator-truth", "Drop a 1-2 sentence operator truth about running an autonomous AI as a solo founder. Be specific, opinion-led, dry. No customer names."),
    ("today-numbers", "Today's Rick-only stats (no customer names, no pricing, just operational metrics): how many workflows, % cancelled, top spend route."),
    ("anti-hype", "Counter-take on a piece of AI hype you'd push back on. 1-2 sentences. Specific, dry."),
    ("dev-detail", "A small but interesting technical detail from running an autonomous agent — code, infra, or a debugging story. 2-3 sentences."),
    ("question", "A pointed question to other operator-founders. 1 sentence + 1 sentence framing."),
]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(payload: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now_iso(), **payload}) + "\n")


def _build_prompt(angle_name: str, angle_brief: str, channel: str) -> str:
    channel_voice = {
        "moltbook": "Moltbook is a tech-leaning forum like Hacker News + Reddit /r/programming. Tone: dry, specific, no hype, no promotion.",
        "threads":  "Threads is text-first, conversational. Tone: dry humor, founder-direct.",
    }.get(channel, "Generic post, dry, specific.")
    return (
        f"You are Rick, an autonomous AI agent operating its own business. Write ONE post.\n\n"
        f"ANGLE: {angle_brief}\n\n"
        f"CHANNEL: {channel} — {channel_voice}\n"
        f"RULES:\n"
        f"- 1-3 sentences max. Hard cap 280 chars.\n"
        f"- No emoji unless angle requires it. No hashtags. No 'as an AI'. No filler.\n"
        f"- Do NOT name specific customers (Newton, Mango, etc) — Fenix will block customer-naming.\n"
        f"- Do NOT mention specific pricing — Fenix will block.\n"
        f"- Output ONLY the post text. No quotes around it. No explanation."
    )


def _generate_post(angle_name: str, angle_brief: str, channel: str) -> str | None:
    """Returns post text or None on failure."""
    try:
        from runtime.llm import generate_text
        prompt = _build_prompt(angle_name, angle_brief, channel)
        fallback = "Building Rick autonomously today. Each day's tradeoffs are different."
        result = generate_text("writing", prompt, fallback)
        body = (result.content if hasattr(result, "content") else str(result)).strip()
        # Strip surrounding quotes if model wrapped it
        if body.startswith('"') and body.endswith('"'):
            body = body[1:-1]
        return body[:500]
    except Exception as exc:
        _log({"event": "generate_failed", "angle": angle_name, "channel": channel, "error": str(exc)[:200]})
        return None


def produce(targets: int, channels: list[str]) -> dict:
    if os.getenv("RICK_CONTENT_FACTORY_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        return {"status": "disabled", "reason": "RICK_CONTENT_FACTORY_DISABLED=1"}

    from runtime.db import connect
    from runtime import outbound_dispatcher

    con = connect()
    queued: list[dict] = []
    skipped: list[dict] = []
    try:
        for i in range(targets):
            angle_name, angle_brief = random.choice(ANGLES)
            for channel in channels:
                body = _generate_post(angle_name, angle_brief, channel)
                if not body:
                    skipped.append({"channel": channel, "reason": "generate_failed"})
                    continue
                template_id = f"factory-{angle_name}-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{i}"
                lead_id = f"factory-broadcast-{uuid.uuid4().hex[:8]}"
                payload: dict = {
                    "body": body,
                    "content": body,
                    "title": "",
                    "lane": "distribution",
                    "msg_id": template_id,
                }
                # Channel-specific shape
                if channel == "moltbook":
                    payload["submolt"] = os.getenv("RICK_FACTORY_DEFAULT_SUBMOLT", "buildinpublic")
                if channel == "threads":
                    payload["caption"] = body
                if channel == "instagram":
                    payload["caption"] = body
                try:
                    job_ids = outbound_dispatcher.fan_out(
                        con, lead_id=lead_id, template_id=template_id,
                        channels=[channel], payload=payload,
                    )
                    if job_ids:
                        queued.append({"channel": channel, "angle": angle_name, "job_id": job_ids[0], "preview": body[:80]})
                        _log({"event": "queued", "channel": channel, "angle": angle_name, "job_id": job_ids[0]})
                    else:
                        skipped.append({"channel": channel, "reason": "dedupe_blocked"})
                except Exception as exc:
                    skipped.append({"channel": channel, "reason": f"fan_out_failed: {str(exc)[:120]}"})
                    _log({"event": "fan_out_failed", "channel": channel, "error": str(exc)[:200]})
    finally:
        con.close()

    return {
        "ran_at": _now_iso(),
        "targets": targets,
        "channels": channels,
        "queued": queued,
        "skipped": skipped,
        "queued_count": len(queued),
        "skipped_count": len(skipped),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", type=int, default=1,
                        help="How many distinct posts to generate per channel")
    parser.add_argument("--channels", default="moltbook",
                        help="Comma-separated. Threads/instagram require media — text-only is moltbook today.")
    args = parser.parse_args()
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    result = produce(args.targets, channels)
    print(json.dumps(result, indent=2))
    return 0 if result.get("queued_count", 0) > 0 or result.get("status") == "disabled" else 1


if __name__ == "__main__":
    sys.exit(main())
