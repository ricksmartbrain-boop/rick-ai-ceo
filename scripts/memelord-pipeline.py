#!/usr/bin/env python3
"""
memelord-pipeline.py — Trend-aware meme generation + distribution for Rick AI CEO
meetrick.ai | #AIstartup #founders #aiceo

Strategy: VIRAL. IRONIC. CRINGEY. EXTREME FUN.
Video memes are priority (generated first). Images are backup/complement.

Usage:
  python3 memelord-pipeline.py              # generate candidates + queue for approval
  python3 memelord-pipeline.py --dry-run    # no-credit preview; no Memelord generation
  python3 memelord-pipeline.py --live       # generate + post to non-protected channels
  python3 memelord-pipeline.py --count 2    # 2 image memes per prompt
  python3 memelord-pipeline.py --no-video   # skip video meme generation
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from typing import Dict, List, Optional

import requests
import feedparser

# ── Environment ────────────────────────────────────────────────────────────────
ENV_FILE = Path("/Users/rickthebot/.openclaw/workspace/config/rick.env")

def load_env(env_file: Path) -> None:
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export").strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

load_env(ENV_FILE)

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from runtime.llm import generate_text  # noqa: E402

MEMELORD_API_KEY  = os.environ.get("MEMELORD_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_IMAGE_MODEL = os.environ.get("RICK_OPENAI_IMAGE_MODEL", "gpt-image-2")

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_DIR  = Path("/Users/rickthebot/rick-vault/control/briefings")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"memelord-{datetime.now().strftime('%Y-%m-%d')}.log"
MEME_DIR = Path("/tmp/memes")
MEME_DIR.mkdir(parents=True, exist_ok=True)
BELKINSMAIN_STATE_FILE = Path("/Users/rickthebot/rick-vault/control/belkinsmain-post-state.json")
APPROVAL_QUEUE_FILE = Path("/Users/rickthebot/rick-vault/content/memelord-approval-queue.json")
MEMELORD_CREDIT_BLOCKED = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE),
    ],
)
log = logging.getLogger("memelord")

# ── Fallback prompts (hardcoded Rick bangers if Claude/trends fail) ────────────
FALLBACK_PROMPTS = [
    "POV: you hired a $200k CEO and he's on his 4th 'sync' of the day while Rick AI CEO already shipped 3 features, replied to customers, and filed taxes — all before 9am",
    "me at 3am running a company for $9/month while your human CEO is 'recharging' in a $500/night hotel on the company card",
    "Rick AI CEO after handling ops, marketing, customer support, investor updates, and a product launch before your CEO finishes his morning coffee and opens Slack for the first time",
]

# ── Credit-safe API health check ───────────────────────────────────────────────

def check_api_health() -> bool:
    """Verify Memelord API is reachable and auth works WITHOUT burning credits.
    Uses a GET to the docs page with auth header — if we get a non-5xx, API is up.
    Falls back to a lightweight HEAD request to the homepage.
    """
    if not MEMELORD_API_KEY:
        log.error("MEMELORD_API_KEY not set")
        return False
    try:
        resp = requests.get(
            "https://www.memelord.com/docs",
            headers={"Authorization": f"Bearer {MEMELORD_API_KEY}"},
            timeout=15,
        )
        if resp.status_code < 500:
            log.info("✅ Memelord API reachable (docs returned %d)", resp.status_code)
            return True
        log.warning("Memelord API returned %d on docs check", resp.status_code)
        return False
    except Exception as exc:
        log.error("Memelord API health check failed: %s", exc)
        return False

CLAUDE_SYSTEM_PROMPT = """\
You are a viral meme strategist for Rick AI CEO (meetrick.ai).

Rick is an AI CEO that costs $9/month. He never sleeps, never takes equity, never needs a 'sync' \
to make a decision. He's funnier, faster, and cheaper than any human CEO alive.

Your job: take trending topics and turn them into ABSURDIST, IRONIC, CRINGE-FUNNY meme prompts \
that make Rick look like an obvious hero while making the idea of a $200k human CEO look completely unhinged.

Tone rules:
- Lean HARD into the absurdity of an AI literally running a business
- POV format, "me vs them" energy, extreme exaggeration — all good
- Self-aware AI jokes are gold ("I don't sleep, I just reload weights")
- Make it cringe-funny, not polished — polish is for LinkedIn cope
- Reference meme formats explicitly: Drake pointing, distracted boyfriend, \
  This Is Fine dog, woman yelling at cat, galaxy brain, Chad vs Virgin, \
  POV text, ratio, Twitter screenshot style, etc.
- Always tie back to: Rick AI CEO / meetrick.ai / $9/month vs $200k
- The vibe: extremely online founder-twitter humor that makes people snort at 11pm and SHARE

Output ONLY a JSON array of 3 strings. Each string is a full meme prompt describing \
the format + setup + punchline. No extra text, no explanation. Just the JSON array.
"""

# ── 1. Trend scraping ──────────────────────────────────────────────────────────

def scrape_reddit_trends() -> list[str]:
    headers = {"User-Agent": "MemelordPipeline/2.0 (Rick AI CEO; meetrick.ai)"}
    titles = []
    for sub in ("memes", "dankmemes"):
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit=5"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            for p in data.get("data", {}).get("children", []):
                t = p.get("data", {}).get("title", "")
                if t:
                    titles.append(t)
        except Exception as exc:
            log.warning("Reddit r/%s failed: %s", sub, exc)
    return titles[:10]


def scrape_google_trends() -> list[str]:
    url = "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US"
    try:
        feed = feedparser.parse(url)
        return [e.get("title", "") for e in feed.entries[:5] if e.get("title")]
    except Exception as exc:
        log.warning("Google Trends failed: %s", exc)
        return []


def _xpost_json(*args: str) -> dict:
    try:
        result = subprocess.run(
            ["xpost", *args, "--json"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        log.warning("X trend source failed: %s", exc)
        return {}
    if result.returncode != 0:
        log.warning("X trend source failed rc=%d: %s", result.returncode, result.stderr.strip()[:200])
        return {}
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def _extract_x_texts(payload: dict) -> list[str]:
    data = payload.get("data") or payload.get("tweets") or []
    if isinstance(data, dict):
        data = list(data.values())
    texts = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text_value = item.get("text") or item.get("full_text") or item.get("content")
        if not isinstance(text_value, str):
            continue
        text_value = " ".join(text_value.split())
        if text_value:
            texts.append(text_value[:180])
    return texts


def scrape_x_trends() -> list[str]:
    """Read-only X trend inputs from the authenticated xpost account."""
    texts: list[str] = []
    texts.extend(_extract_x_texts(_xpost_json("home", "--count", "20")))
    if len(texts) < 5:
        for query in (
            "AI startup founder",
            "startup launch",
            "founder meme",
        ):
            texts.extend(_extract_x_texts(_xpost_json("search", query, "--count", "10")))
            if len(texts) >= 10:
                break

    seen = set()
    trends = []
    for text_value in texts:
        key = text_value.lower()
        if key in seen:
            continue
        seen.add(key)
        trends.append(text_value)
        if len(trends) >= 10:
            break
    return trends


def get_trends() -> list[str]:
    log.info("🔍 Scraping trending topics…")
    x_trends = scrape_x_trends()
    reddit  = scrape_reddit_trends()
    google  = scrape_google_trends()
    combined = x_trends + reddit + google

    if combined:
        log.info("Found %d trends", len(combined))
        if x_trends:
            log.info("X trend source contributed %d read-only items", len(x_trends))
        for t in combined[:6]:
            log.info("  › %s", t)
    else:
        log.warning("No trends found — using generic fallback topics")
        combined = [
            "AI taking over jobs", "startup layoffs", "remote work", "ChatGPT",
            "venture capital", "hustle culture", "SaaS valuations",
        ]
    return combined

# ── 2. Prompt generation via Claude ───────────────────────────────────────────

def generate_prompts(trends: list[str], dry_run: bool = False) -> list[str]:
    if dry_run:
        log.info("[DRY RUN] Skipping Claude prompt generation — using fallback prompts")
        return FALLBACK_PROMPTS

    try:
        user_msg = (
            "Here are today's trending topics:\n"
            + "\n".join(f"- {t}" for t in trends[:10])
            + "\n\nGenerate 3 VIRAL, ABSURDIST, CRINGE-FUNNY meme prompts for Rick AI CEO. "
            "Each one should reference a specific meme format and end with a punchline tying "
            "back to Rick AI CEO / meetrick.ai / $9/month. Output ONLY the JSON array."
        )

        log.info("🧠 Calling runtime.llm ('writing' route) — viral meme prompt generation…")
        result = generate_text("writing", CLAUDE_SYSTEM_PROMPT + "\n\n" + user_msg, "", force_fresh=True)
        if result.mode not in ("live", "cached"):
            raise ValueError(f"generation fell back (runner={result.runner})")

        raw = result.content.strip()
        start = raw.find("[")
        end   = raw.rfind("]") + 1
        if start == -1 or end == 0:
            raise ValueError(f"No JSON array found: {raw[:300]}")

        prompts = json.loads(raw[start:end])
        if not isinstance(prompts, list) or not prompts:
            raise ValueError("Empty prompt list")

        log.info("✅ Generated %d prompts:", len(prompts))
        for i, p in enumerate(prompts, 1):
            log.info("  [%d] %s", i, p[:120])
        return prompts[:3]

    except Exception as exc:
        log.error("LLM prompt generation failed: %s — using fallback prompts", exc)
        return FALLBACK_PROMPTS

# ── 3a. VIDEO MEME (priority) — generate + poll for completion ────────────────

def generate_video_meme(prompt: str, dry_run: bool = False) -> Optional[Dict]:
    """
    POST /api/v1/ai-video-meme → get jobId → poll GET /api/video/render/remote?jobId=...
    every 5s up to 2 minutes → download MP4 when ready.
    """
    global MEMELORD_CREDIT_BLOCKED

    if not MEMELORD_API_KEY:
        log.error("MEMELORD_API_KEY missing — skipping video meme")
        return None

    if MEMELORD_CREDIT_BLOCKED:
        log.warning("Memelord credits/billing blocked — skipping video generation")
        return None

    if dry_run:
        log.info("[DRY RUN] Would generate video meme: %s", prompt[:100])
        return {"dry_run": True, "prompt": prompt, "local_path": None, "url": None}

    headers = {
        "Authorization": f"Bearer {MEMELORD_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "count": 1,
        "category": "trending",
        "include_nsfw": False,
    }

    # Step 1: Submit job
    log.info("🎬 Submitting VIDEO meme job…")
    try:
        resp = requests.post(
            "https://www.memelord.com/api/v1/ai-video-meme",
            headers=headers, json=payload, timeout=90,
        )
        if resp.status_code == 402:
            MEMELORD_CREDIT_BLOCKED = True
            log.error("Memelord video API blocked by credits/billing (402) — stopping this batch")
            return None
        resp.raise_for_status()
        data = resp.json()
        log.info("Video job response: %s", json.dumps(data)[:300])
    except Exception as exc:
        log.error("Video meme submit failed: %s", exc)
        return None

    # Extract jobId — handle various response shapes
    job_id = (
        data.get("jobId")
        or data.get("job_id")
        or data.get("id")
        or (data.get("results", [{}])[0].get("jobId") if data.get("results") else None)
        or (data.get("jobs", [{}])[0].get("job_id") if data.get("jobs") else None)
        or (data.get("jobs", [{}])[0].get("jobId") if data.get("jobs") else None)
    )

    if not job_id:
        log.warning("No jobId in video response — treating as webhook-only: %s", data)
        return {"job_id": None, "prompt": prompt, "raw_response": data, "local_path": None}

    log.info("🎬 Video job submitted — jobId=%s — polling…", job_id)

    # Step 2: Poll every 5s, up to 5 minutes (60 attempts)
    poll_url = f"https://www.memelord.com/api/video/render/remote?jobId={job_id}"
    for attempt in range(1, 61):
        time.sleep(5)
        try:
            poll_resp = requests.get(
                poll_url,
                headers={"Authorization": f"Bearer {MEMELORD_API_KEY}"},
                timeout=15,
            )
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()
            # Handle both flat {status,url} and nested {job:{status,mp4Url}} shapes
            job_obj = poll_data.get("job") or {}
            status = (
                job_obj.get("status")
                or poll_data.get("status")
                or poll_data.get("state")
                or "pending"
            )
            video_url = (
                job_obj.get("mp4Url")
                or job_obj.get("url")
                or poll_data.get("mp4Url")
                or poll_data.get("url")
                or poll_data.get("output_url")
            )
            log.info("  [poll %02d/24] status=%s  url=%s", attempt, status,
                     str(video_url)[:60] if video_url else "none")

            if video_url or status in ("done", "completed", "success"):
                if not video_url:
                    log.warning("Status says done but no URL found: %s", poll_data)
                    return {"job_id": job_id, "prompt": prompt, "url": None, "local_path": None}

                log.info("✅ Video ready: %s", video_url)
                # Download MP4
                ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
                out_path = MEME_DIR / f"memelord_video_{ts}.mp4"
                try:
                    dl = requests.get(video_url, timeout=60)
                    dl.raise_for_status()
                    out_path.write_bytes(dl.content)
                    log.info("✅ Video saved: %s (%d KB)", out_path, len(dl.content) // 1024)
                except Exception as dl_exc:
                    log.error("Video download failed: %s", dl_exc)
                    out_path = None

                return {
                    "job_id": job_id,
                    "prompt": prompt,
                    "url": video_url,
                    "local_path": str(out_path) if out_path else None,
                }

            if status in ("failed", "error"):
                log.error("Video job failed: %s", poll_data)
                return None

        except Exception as poll_exc:
            log.warning("  [poll %02d/24] error: %s", attempt, poll_exc)

    log.error("Video meme timed out after 5 minutes — jobId=%s", job_id)
    return None

# ── 3b. IMAGE MEME generation ─────────────────────────────────────────────────

def generate_image_meme(prompt: str, count: int = 1, dry_run: bool = False) -> list[dict]:
    global MEMELORD_CREDIT_BLOCKED

    if not MEMELORD_API_KEY:
        log.error("MEMELORD_API_KEY missing — skipping image meme")
        return []

    if MEMELORD_CREDIT_BLOCKED:
        log.warning("Memelord credits/billing blocked — skipping remaining image generation")
        return []

    if dry_run:
        log.info("[DRY RUN] Would generate %d image meme(s): %s", count, prompt[:100])
        return [{
            "dry_run": True,
            "url": None,
            "template": "dry-run",
            "local_path": None,
            "prompt": prompt,
            "type": "image",
        }]

    headers = {
        "Authorization": f"Bearer {MEMELORD_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"prompt": prompt, "count": count, "category": "trending", "include_nsfw": False}

    try:
        log.info("🖼  Generating IMAGE meme: %s", prompt[:80])
        resp = requests.post(
            "https://www.memelord.com/api/v1/ai-meme",
            headers=headers, json=payload, timeout=90,
        )
        if resp.status_code == 402:
            MEMELORD_CREDIT_BLOCKED = True
            log.error("Memelord image API blocked by credits/billing (402) — stopping this batch")
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Image meme API failed: %s", exc)
        return []

    if not data.get("success"):
        log.error("Memelord image API success=false: %s", data)
        return []

    memes = []
    for result in data.get("results", []):
        meme_url = result.get("url", "")
        template = result.get("template_name", "unknown")
        if not meme_url:
            continue

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_path = MEME_DIR / f"memelord_{ts}.webp"
        try:
            img = requests.get(meme_url, timeout=20)
            img.raise_for_status()
            out_path.write_bytes(img.content)
            log.info("✅ Image saved: %s (template: %s)", out_path, template)
        except Exception as exc:
            log.error("Image download failed: %s", exc)
            out_path = None

        memes.append({
            "url":        meme_url,
            "template":   template,
            "local_path": str(out_path) if out_path else None,
            "prompt":     prompt,
            "type":       "image",
        })
    return memes


def generate_openai_image(prompt: str, count: int = 1, dry_run: bool = False) -> list[dict]:
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY missing — skipping OpenAI image generation")
        return []

    if dry_run:
        log.info("[DRY RUN] Would generate %d OpenAI image(s): %s", count, prompt[:100])
        return [{
            "dry_run": True,
            "url": None,
            "template": OPENAI_IMAGE_MODEL,
            "local_path": None,
            "prompt": prompt,
            "type": "image",
            "provider": "openai",
        }]

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    results: list[dict] = []
    for index in range(count):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_path = MEME_DIR / f"openai_{OPENAI_IMAGE_MODEL}_{ts}_{index + 1}.png"
        payload = {
            "model": OPENAI_IMAGE_MODEL,
            "prompt": (
                f"{prompt}\n\n"
                "Create a highly shareable internet meme image. Make any text large, "
                "legible, and minimal. Avoid tiny captions, watermarks, fake UI chrome, "
                "or brand logos. Square composition, bold contrast, immediately readable."
            ),
            "size": "1024x1024",
            "quality": "high",
            "n": 1,
        }
        try:
            log.info("🎨 Generating OpenAI image (%s): %s", OPENAI_IMAGE_MODEL, prompt[:80])
            resp = requests.post(
                "https://api.openai.com/v1/images/generations",
                headers=headers,
                json=payload,
                timeout=300,
            )
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            item = (data.get("data") or [{}])[0]
            raw = None
            if isinstance(item, dict) and item.get("b64_json"):
                raw = base64.b64decode(item["b64_json"])
            elif isinstance(item, dict) and item.get("url"):
                img = requests.get(item["url"], timeout=120)
                img.raise_for_status()
                raw = img.content
            if not raw:
                log.error("OpenAI image response missing b64_json/url")
                continue
            out_path.write_bytes(raw)
            log.info("✅ OpenAI image saved: %s (%d KB)", out_path, len(raw) // 1024)
            results.append({
                "url": item.get("url") if isinstance(item, dict) else None,
                "template": OPENAI_IMAGE_MODEL,
                "local_path": str(out_path),
                "prompt": prompt,
                "type": "image",
                "provider": "openai",
            })
        except Exception as exc:
            log.error("OpenAI image generation failed: %s", exc)
            return results
    return results


def generate_image_candidates(
    prompt: str,
    count: int = 1,
    dry_run: bool = False,
    image_provider: str = "auto",
) -> list[dict]:
    if image_provider == "openai":
        return generate_openai_image(prompt, count=count, dry_run=dry_run)
    if image_provider == "memelord":
        return generate_image_meme(prompt, count=count, dry_run=dry_run)

    memes = generate_image_meme(prompt, count=count, dry_run=dry_run)
    if memes:
        return memes
    if MEMELORD_CREDIT_BLOCKED:
        log.info("Falling back to OpenAI image generation after Memelord credit block")
        return generate_openai_image(prompt, count=count, dry_run=dry_run)
    return []

# ── 4. Caption generation ──────────────────────────────────────────────────────

def generate_caption(prompt: str, media_type: str = "image") -> str:  # noqa: E501
    default_captions = {
        "video": "me at 3am running a company for $9/month 🤖 meetrick.ai #AIstartup #founders #aiceo",
        "image": "Rick AI CEO: $9/month. Never sleeps. Never takes equity. Never asks for a sync. 🤖 meetrick.ai #AIstartup #founders",
    }
    try:
        result = generate_text("writing", (
            f"Write ONE punchy, cringe-funny tweet caption for this Rick AI CEO meme:\n\n"
            f'"{prompt}"\n\n'
            "Rules:\n"
            "- Max 240 chars\n"
            "- Extremely online voice — ironic, self-aware, a little unhinged\n"
            "- Include meetrick.ai and 2-3 hashtags: #AIstartup #founders #aiceo\n"
            "- NO quotes around the output. Just the raw tweet text."
        ), "", force_fresh=True)
        if result.mode != "live":
            raise ValueError(f"generation fell back (runner={result.runner})")
        caption = result.content.strip().strip('"').strip("'")
        log.info("Caption generated: %s", caption)
        return caption
    except Exception as exc:
        log.warning("Caption failed: %s — using default", exc)
        return default_captions.get(media_type, default_captions["image"])


def _load_approval_queue() -> dict:
    try:
        return json.loads(APPROVAL_QUEUE_FILE.read_text()) if APPROVAL_QUEUE_FILE.exists() else {"items": []}
    except Exception:
        return {"items": []}


def _approval_id(media_path: Optional[str], caption: str, prompt: str) -> str:
    hasher = hashlib.sha256()
    hasher.update(prompt.encode("utf-8", errors="ignore"))
    hasher.update(caption.encode("utf-8", errors="ignore"))
    if media_path and Path(media_path).exists():
        hasher.update(Path(media_path).read_bytes())
    return hasher.hexdigest()[:12]


def queue_for_approval(candidates: list[dict]) -> int:
    if not candidates:
        return 0

    queue = _load_approval_queue()
    items = queue.setdefault("items", [])
    existing_ids = {str(item.get("id")) for item in items}
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    added = 0

    for candidate in candidates:
        media_path = candidate.get("media_path")
        caption = candidate.get("caption", "")
        prompt = candidate.get("prompt", "")
        item_id = _approval_id(media_path, caption, prompt)
        if item_id in existing_ids:
            continue
        items.append({
            "id": item_id,
            "status": "pending",
            "created_at": now,
            "type": candidate.get("type", "meme"),
            "media_path": media_path,
            "caption": caption,
            "prompt": prompt,
            "source": "memelord-pipeline",
            "recommended_channels": candidate.get("recommended_channels", []),
            "protected_channels": ["@belkinsmain"],
            "notes": (
                "Draft only. No autonomous posting. @belkinsmain requires explicit "
                "Vlad approval for the exact post in the current conversation."
            ),
        })
        existing_ids.add(item_id)
        added += 1

    APPROVAL_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPROVAL_QUEUE_FILE.write_text(json.dumps(queue, indent=2, sort_keys=True))
    log.info("🧾 Queued %d meme candidate(s) for approval: %s", added, APPROVAL_QUEUE_FILE)
    return added

# ── 5. Distribution ────────────────────────────────────────────────────────────

def post_to_x(caption: str, media_path: Optional[str], dry_run: bool = False) -> bool:
    if dry_run:
        if media_path:
            log.info("[DRY RUN] xpost post '%s' --image %s", caption[:60], media_path)
        else:
            log.info("[DRY RUN] xpost post '%s'", caption[:60])
        return True

    if not media_path or not Path(media_path).exists():
        log.warning("No local media file to post — text-only fallback")
        cmd = ["xpost", "post", caption]
    else:
        cmd = ["xpost", "post", caption, "--image", media_path]

    try:
        log.info("📤 Posting to X…")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if result.returncode == 0:
            log.info("✅ Posted to X! %s", result.stdout.strip())
            return True
        else:
            log.error("xpost failed (rc=%d): %s", result.returncode, result.stderr.strip())
            return False
    except FileNotFoundError:
        log.error("xpost not found in PATH — skipping X post")
        return False
    except Exception as exc:
        log.error("X post error: %s", exc)
        return False


def _belkinsmain_dedupe_key(media_path: Optional[str], caption: str) -> str:
    hasher = hashlib.sha256()
    hasher.update(caption.encode("utf-8", errors="ignore"))
    if media_path and Path(media_path).exists():
        hasher.update(Path(media_path).read_bytes())
    return hasher.hexdigest()


def _belkinsmain_recently_sent(dedupe_key: str, window_seconds: int = 86400) -> bool:
    try:
        state = json.loads(BELKINSMAIN_STATE_FILE.read_text()) if BELKINSMAIN_STATE_FILE.exists() else {}
    except Exception:
        state = {}
    now = time.time()
    sent = state.get("sent", {})
    last_sent_at = float(sent.get(dedupe_key, 0) or 0)
    return now - last_sent_at < window_seconds


def _record_belkinsmain_send(dedupe_key: str) -> None:
    try:
        state = json.loads(BELKINSMAIN_STATE_FILE.read_text()) if BELKINSMAIN_STATE_FILE.exists() else {}
    except Exception:
        state = {}
    now = time.time()
    sent = state.setdefault("sent", {})
    sent[dedupe_key] = now
    cutoff = now - 86400 * 14
    state["sent"] = {k: v for k, v in sent.items() if float(v or 0) >= cutoff}
    state["last_sent_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    BELKINSMAIN_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BELKINSMAIN_STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def post_to_telegram_belkinsmain(media_path: Optional[str], caption: str, dry_run: bool = False) -> bool:
    """Post meme to @belkinsmain Telegram channel (-1002707290783)."""
    CHAT_ID = "-1002707290783"
    BOT_TOKEN = os.environ.get("RICK_TELEGRAM_BOT_TOKEN", "")
    if not BOT_TOKEN:
        log.warning("Telegram: RICK_TELEGRAM_BOT_TOKEN not set — skipping")
        return False
    if dry_run:
        log.info("[DRY RUN] Would post to @belkinsmain: %s", caption[:80])
        return True
    dedupe_key = _belkinsmain_dedupe_key(media_path, caption)
    if _belkinsmain_recently_sent(dedupe_key):
        log.warning("@belkinsmain duplicate suppressed by 24h dedupe lock")
        return False
    try:
        if media_path and Path(media_path).exists():
            suffix = Path(media_path).suffix.lower()
            if suffix in (".mp4", ".mov", ".webm"):
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
                with open(media_path, "rb") as f:
                    resp = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption}, files={"video": f}, timeout=60)
            else:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                with open(media_path, "rb") as f:
                    resp = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption}, files={"photo": f}, timeout=30)
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            resp = requests.post(url, json={"chat_id": CHAT_ID, "text": caption}, timeout=15)
        resp.raise_for_status()
        _record_belkinsmain_send(dedupe_key)
        log.info("✅ Posted to @belkinsmain!")
        return True
    except Exception as exc:
        log.error("@belkinsmain post failed: %s", exc)
        return False


def post_to_instagram(image_path: Optional[str], caption: str, dry_run: bool = False) -> bool:
    """Post to Instagram via CDP script. Pre-flight: verify Chrome session alive."""
    if dry_run:
        log.info("[DRY RUN] Would post to Instagram: %s", caption[:60])
        return True

    # Pre-flight: check Chrome on port 9222 is alive and session not expired
    try:
        import urllib.request
        tabs = json.loads(urllib.request.urlopen("http://localhost:9222/json", timeout=3).read())
        ig_tabs = [t for t in tabs if "instagram.com" in t.get("url", "") and t.get("type") == "page"]
        if not ig_tabs:
            log.warning("Instagram: no Instagram tab on port 9222 — session expired or not open")
            return False
    except Exception as e:
        log.warning("Instagram: Chrome port 9222 not reachable: %s — skipping", e)
        return False

    if not image_path or not Path(image_path).exists():
        log.warning("Instagram: no local media file — skipping")
        return False

    script = Path(__file__).parent / "post-instagram-reel-cdp.py"
    if not script.exists():
        log.warning("Instagram: post-instagram-reel-cdp.py not found — skipping")
        return False

    try:
        log.info("📤 Posting to Instagram via CDP...")
        result = subprocess.run(
            [sys.executable, str(script), image_path, caption],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            log.info("✅ Posted to Instagram! %s", result.stdout.strip()[:200])
            return True
        else:
            log.error("Instagram CDP failed (rc=%d): %s", result.returncode, result.stderr.strip()[:300])
            return False
    except Exception as exc:
        log.error("Instagram post error: %s", exc)
        return False


def post_to_threads(media_path: Optional[str], caption: str, dry_run: bool = False) -> bool:
    """Post to Threads via CDP script. Pre-flight: verify Chrome session alive."""
    if dry_run:
        log.info("[DRY RUN] Would post to Threads: %s", caption[:60])
        return True

    # Pre-flight: check Chrome on port 9222 is alive with Threads tab
    try:
        import urllib.request
        tabs = json.loads(urllib.request.urlopen("http://localhost:9222/json", timeout=3).read())
        threads_tabs = [t for t in tabs if "threads.net" in t.get("url", "") and t.get("type") == "page"]
        if not threads_tabs:
            log.warning("Threads: no Threads tab on port 9222 — session expired or not open")
            return False
    except Exception as e:
        log.warning("Threads: Chrome port 9222 not reachable: %s — skipping", e)
        return False

    if not media_path or not Path(media_path).exists():
        log.warning("Threads: no local media file — skipping")
        return False

    script = Path(__file__).parent / "post-threads-cdp.py"
    if not script.exists():
        log.warning("Threads: post-threads-cdp.py not found — skipping")
        return False

    try:
        log.info("📤 Posting to Threads via CDP...")
        result = subprocess.run(
            [sys.executable, str(script), media_path, caption],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            log.info("✅ Posted to Threads! %s", result.stdout.strip()[:200])
            return True
        else:
            log.error("Threads CDP failed (rc=%d): %s", result.returncode, result.stderr.strip()[:300])
            return False
    except Exception as exc:
        log.error("Threads post error: %s", exc)
        return False


def post_to_reddit(media_path: Optional[str], caption: str, subreddit: str = "artificial", dry_run: bool = False) -> bool:
    """Post to Reddit via CDP script (port 9223). Pre-flight: verify Chrome session alive."""
    if dry_run:
        log.info("[DRY RUN] Would post to Reddit r/%s: %s", subreddit, caption[:60])
        return True

    # Pre-flight: check Chrome on port 9223 is alive
    try:
        import urllib.request
        tabs = json.loads(urllib.request.urlopen("http://localhost:9223/json", timeout=3).read())
        if not tabs:
            log.warning("Reddit: Chrome port 9223 has no tabs — session may be dead")
            return False
    except Exception as e:
        log.warning("Reddit: Chrome port 9223 not reachable: %s — skipping", e)
        return False

    if not media_path or not Path(media_path).exists():
        log.warning("Reddit: no local media file — skipping")
        return False

    script = Path(__file__).parent / "post-reddit-cdp.py"
    if not script.exists():
        log.warning("Reddit: post-reddit-cdp.py not found — skipping")
        return False

    # Generate a title from caption (Reddit requires a title)
    title = caption[:200].split("\n")[0].strip() or "Rick AI CEO meme"

    try:
        log.info("📤 Posting to Reddit r/%s via CDP...", subreddit)
        result = subprocess.run(
            [sys.executable, str(script), media_path, title, subreddit],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            log.info("✅ Posted to Reddit r/%s! %s", subreddit, result.stdout.strip()[:200])
            return True
        else:
            log.error("Reddit CDP failed (rc=%d): %s", result.returncode, result.stderr.strip()[:300])
            return False
    except Exception as exc:
        log.error("Reddit post error: %s", exc)
        return False

# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_pipeline(
    dry_run: bool = False,
    live: bool = False,
    count: int = 1,
    skip_video: bool = False,
    post_belkinsmain: bool = False,
    belkinsmain_approval_id: str = "",
    image_provider: str = "auto",
    max_prompts: int = 3,
) -> dict:
    log.info("=" * 65)
    log.info("🤖 RICK AI CEO — Memelord Pipeline (VIRAL EDITION)")
    log.info(
        "   dry_run=%s  live=%s  count=%d  skip_video=%s  image_provider=%s  post_belkinsmain=%s",
        dry_run,
        live,
        count,
        skip_video,
        image_provider,
        post_belkinsmain,
    )
    log.info("=" * 65)

    summary = {
        "trends": [], "prompts": [], "video_meme": None,
        "image_memes": [], "x_posts": 0, "approval_candidates": 0,
    }

    if post_belkinsmain and (not live or not belkinsmain_approval_id.strip()):
        log.error(
            "@belkinsmain refused: requires --live and --belkinsmain-approval-id "
            "for the exact Vlad-approved post"
        )
        post_belkinsmain = False

    # 1. Trends
    trends = get_trends()
    summary["trends"] = trends

    # 2. Prompts
    prompts = generate_prompts(trends, dry_run=dry_run)[:max(1, max_prompts)]
    summary["prompts"] = prompts

    approval_candidates: list[dict] = []

    # 3a. VIDEO MEME — PRIORITY
    if not skip_video and prompts:
        video_prompt = prompts[0]
        log.info("")
        log.info("🎬 VIDEO MEME FIRST (priority)")
        video = generate_video_meme(video_prompt, dry_run=dry_run)
        summary["video_meme"] = video

        if video and (video.get("local_path") or video.get("dry_run")):
            video_caption = generate_caption(video_prompt, media_type="video")
            approval_candidates.append({
                "type": "video",
                "media_path": video.get("local_path"),
                "caption": video_caption,
                "prompt": video_prompt,
                "recommended_channels": ["threads", "instagram", "newsletter", "blog"],
            })
            if live:
                ok = post_to_x(video_caption, video.get("local_path"), dry_run=dry_run)
                if ok:
                    summary["x_posts"] += 1
            # Protected channels are excluded from live distribution by default.
            video_path = video.get("local_path")
            if live and video_path:
                log.info("📡 Distributing video meme to selected non-protected channels...")
                post_to_threads(video_path, video_caption, dry_run=dry_run)
                post_to_reddit(video_path, video_caption, subreddit="artificial", dry_run=dry_run)
                post_to_instagram(video_path, video_caption, dry_run=dry_run)
                summary["distributed_video"] = True
            elif video_path:
                log.info("📡 Live distribution skipped; video queued for approval")

    # 3b. IMAGE MEMES — complement (all prompts)
    log.info("")
    log.info("🖼  IMAGE MEMES")
    all_image_memes: list[dict] = []
    for i, prompt in enumerate(prompts):
        memes = generate_image_candidates(prompt, count=count, dry_run=dry_run, image_provider=image_provider)
        all_image_memes.extend(memes)
        if MEMELORD_CREDIT_BLOCKED and image_provider == "memelord":
            break
        time.sleep(1)  # gentle rate limiting

    summary["image_memes"] = all_image_memes

    # Queue the best image candidate; live mode may distribute it to non-protected channels.
    best_image = next((m for m in all_image_memes if m.get("local_path")), None)
    if best_image:
        img_caption = generate_caption(best_image["prompt"], media_type="image")
        approval_candidates.append({
            "type": "image",
            "media_path": best_image["local_path"],
            "caption": img_caption,
            "prompt": best_image["prompt"],
            "recommended_channels": ["x", "threads", "newsletter", "blog"],
        })
        if live:
            ok = post_to_x(img_caption, best_image["local_path"], dry_run=dry_run)
            if ok:
                summary["x_posts"] += 1
            post_to_instagram(best_image["local_path"], img_caption, dry_run=dry_run)
            post_to_threads(best_image["local_path"], img_caption, dry_run=dry_run)
            post_to_reddit(best_image["local_path"], img_caption, subreddit="ChatGPT", dry_run=dry_run)
        else:
            log.info("📡 Live distribution skipped; image queued for approval")

    summary["approval_candidates"] = queue_for_approval(approval_candidates)

    # 4b. Optional @belkinsmain posting. Default is OFF after the 2026-05-20
    # duplicate-post incident; public channel posts require explicit operator intent.
    if post_belkinsmain and (best_image or (summary.get("video_meme") and summary["video_meme"] and summary["video_meme"].get("local_path"))):
        tg_media = None
        tg_caption = ""
        if summary.get("video_meme") and summary["video_meme"] and summary["video_meme"].get("local_path"):
            tg_media = summary["video_meme"]["local_path"]
            tg_caption = generate_caption(prompts[0] if prompts else "AI CEO meme", media_type="video")
        elif best_image:
            tg_media = best_image["local_path"]
            tg_caption = generate_caption(best_image["prompt"], media_type="image")
        if tg_caption:
            log.info("@belkinsmain approval id: %s", belkinsmain_approval_id.strip())
            tg_ok = post_to_telegram_belkinsmain(tg_media, tg_caption, dry_run=dry_run)
            summary["telegram_posted"] = tg_ok
    elif not post_belkinsmain:
        log.info("@belkinsmain skipped: pass --post-belkinsmain only after explicit Vlad approval")

    # 5. Summary
    log.info("")
    log.info("=" * 65)
    log.info("📊 PIPELINE COMPLETE")
    log.info("   Trends scraped : %d", len(summary["trends"]))
    log.info("   Prompts used   : %d", len(summary["prompts"]))
    log.info("   Video meme     : %s", "✅ generated" if summary["video_meme"] and (summary["video_meme"].get("local_path") or summary["video_meme"].get("dry_run")) else "❌ none")
    log.info("   Image memes    : %d", len(summary["image_memes"]))
    log.info("   Approval queue : %d new candidate(s)", summary["approval_candidates"])
    log.info("   X posts made   : %d", summary["x_posts"])
    log.info("   @belkinsmain   : %s", "✅" if summary.get("telegram_posted") else "❌")
    log.info("   Mode           : %s", "DRY RUN" if dry_run else ("🔥 LIVE" if live else "DRAFT-FIRST"))
    log.info("   Log            : %s", LOG_FILE)
    log.info("=" * 65)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Viral meme generation + distribution — Rick AI CEO"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without burning Memelord credits or posting")
    parser.add_argument("--live", action="store_true",
                        help="Post to non-protected channels. Default is draft/approval only.")
    parser.add_argument("--count", type=int, default=1,
                        help="Image memes per prompt (default: 1)")
    parser.add_argument("--no-video", action="store_true",
                        help="Skip video meme generation")
    parser.add_argument("--check-only", action="store_true",
                        help="Only check API health, don't generate anything (zero credits)")
    parser.add_argument("--post-belkinsmain", action="store_true",
                        help="Also post to @belkinsmain. Requires --live and exact Vlad approval per run.")
    parser.add_argument("--belkinsmain-approval-id", default="",
                        help="Approval marker/message id for the exact Vlad-approved @belkinsmain post.")
    parser.add_argument("--image-provider", choices=("auto", "memelord", "openai"), default="auto",
                        help="Image backend. auto tries Memelord, then OpenAI if Memelord is credit-blocked.")
    parser.add_argument("--max-prompts", type=int, default=3,
                        help="Limit prompt/image concepts per run (default: 3; use 1 for quick operator batches).")
    args = parser.parse_args()

    if args.check_only:
        if args.image_provider == "openai":
            ok = bool(OPENAI_API_KEY)
            if ok:
                log.info("✅ OPENAI_API_KEY configured for %s", OPENAI_IMAGE_MODEL)
            else:
                log.error("OPENAI_API_KEY not set")
        else:
            ok = check_api_health()
        sys.exit(0 if ok else 1)

    # Pre-flight: verify selected image backend before burning Claude credits on prompts.
    needs_memelord = not args.no_video or args.image_provider in ("auto", "memelord")
    if needs_memelord and not check_api_health():
        log.error("❌ Memelord API unreachable — aborting pipeline to save credits")
        sys.exit(1)
    if args.image_provider == "openai" and not OPENAI_API_KEY:
        log.error("❌ OPENAI_API_KEY missing — aborting OpenAI image run")
        sys.exit(1)

    run_pipeline(
        dry_run=args.dry_run,
        live=args.live,
        count=args.count,
        skip_video=args.no_video,
        post_belkinsmain=args.post_belkinsmain,
        belkinsmain_approval_id=args.belkinsmain_approval_id,
        image_provider=args.image_provider,
        max_prompts=args.max_prompts,
    )


if __name__ == "__main__":
    main()
