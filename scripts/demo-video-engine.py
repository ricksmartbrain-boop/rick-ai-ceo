#!/usr/bin/env python3
"""Build a weekly Rick demo video from the last 24h of real ops logs.

Outputs:
- narration script
- image prompt bundle
- generated stills
- ElevenLabs narration audio
- MP4 slideshow, if ffmpeg is available

Dry-run mode still composes the narration with opus-4-7 (route='review') so
we can inspect the voiceover without spending image/audio/video budget.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS_DIR = DATA_ROOT / "operations"
PUBLIC_VIDEOS_DIR = Path.home() / "meetrick-content" / "videos"
OUTPUT_DIR = DATA_ROOT / "content" / "demo-videos"
ENV_FILES = [
    ROOT / "config" / "rick.env",
    Path.home() / "clawd" / "config" / "rick.env",
    Path.home() / ".config" / "elevenlabs" / "api_key.env",
    Path.home() / ".config" / "elevenlabs" / "api_key",
]

OPENAI_IMAGE_MODEL = "gpt-image-2"
OPENAI_IMAGE_SIZE = "1536x1024"
OPENAI_IMAGE_QUALITY = "high"
OPENROUTER_MODEL = os.getenv("RICK_DEMO_VIDEO_MODEL", "anthropic/claude-opus-4.7")
OPENROUTER_MAX_TOKENS = int(os.getenv("RICK_DEMO_VIDEO_MAX_TOKENS", "320"))
VOICE_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")
DEFAULT_VOICE_SETTINGS = {
    "stability": 0.45,
    "similarity_boost": 0.8,
    "style": 0.35,
    "use_speaker_boost": True,
}


@dataclass
class LogSummary:
    window_start: str
    window_end: str
    channels_published: list[str]
    content_events: int
    cold_emails_sent: int
    bounces_suppressed: int
    qualified_leads: list[str]
    qualified_lead_count: int
    outbound_sent_by_channel: dict[str, int]
    evidence: list[str]


@dataclass
class DemoBundle:
    date: str
    summary: LogSummary
    narration: str
    image_prompts: list[str]
    work_dir: str
    narration_path: str
    prompts_path: str
    manifest_path: str
    audio_path: str | None = None
    video_path: str | None = None
    public_video_path: str | None = None
    public_url: str | None = None


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    except OSError:
        return


def load_env() -> None:
    for path in ENV_FILES:
        _load_env_file(path)


def require_env(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {key}")
    return value


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows


def _within_window(row: dict[str, Any], cutoff: datetime) -> bool:
    for key in ("ts", "timestamp", "ran_at", "generated_at", "sent_at"):
        dt = _parse_dt(row.get(key))
        if dt:
            return dt >= cutoff
    return False


def summarize_wins(now: datetime) -> LogSummary:
    cutoff = now - timedelta(hours=24)

    content_rows = [row for row in _read_jsonl(OPS_DIR / "content-factory.jsonl") if _within_window(row, cutoff)]
    outbound_rows = [row for row in _read_jsonl(OPS_DIR / "outbound-dispatcher.jsonl") if _within_window(row, cutoff)]
    email_send_rows = [row for row in _read_jsonl(OPS_DIR / "email-sends.jsonl") if _within_window(row, cutoff)]
    bounce_rows = [row for row in _read_jsonl(OPS_DIR / "email-bounces.jsonl") if _within_window(row, cutoff)]
    fenix_rows = [row for row in _read_jsonl(OPS_DIR / "fenix-decisions.jsonl") if _within_window(row, cutoff)]
    ledger_rows = [row for row in _read_jsonl(OPS_DIR / "execution-ledger.jsonl") if _within_window(row, cutoff)]

    content_channels = sorted({row.get("channel", "").strip() for row in content_rows if isinstance(row.get("channel"), str) and row.get("channel", "").strip()})
    outbound_sent_by_channel: dict[str, int] = {}
    for row in outbound_rows:
        if row.get("status") != "sent":
            continue
        channel = (row.get("channel") or "").strip()
        if not channel:
            continue
        outbound_sent_by_channel[channel] = outbound_sent_by_channel.get(channel, 0) + 1

    cold_emails_sent = sum(1 for row in email_send_rows if row.get("status") == "sent")
    bounces_suppressed = sum(1 for row in bounce_rows if row.get("event") == "bounced")

    qualified_leads: list[str] = []
    for row in ledger_rows:
        title = (row.get("title") or "").strip()
        notes = (row.get("notes") or "").strip()
        if row.get("kind") != "completed":
            continue
        if "ghost_completed:" not in notes:
            continue
        if not title.startswith("Ghost-completed workflow auto-finalized: "):
            continue
        qualified_leads.append(title.split(": ", 1)[1])

    evidence: list[str] = []
    evidence.append(f"content_factory_channels={','.join(content_channels) if content_channels else 'none'}")
    evidence.append(f"cold_emails_sent={cold_emails_sent}")
    evidence.append(f"bounces_suppressed={bounces_suppressed}")
    evidence.append(f"fenix_decisions={len(fenix_rows)}")
    evidence.append(f"qualified_leads={len(qualified_leads)}")

    return LogSummary(
        window_start=cutoff.isoformat(timespec="seconds"),
        window_end=now.isoformat(timespec="seconds"),
        channels_published=content_channels,
        content_events=len(content_rows),
        cold_emails_sent=cold_emails_sent,
        bounces_suppressed=bounces_suppressed,
        qualified_leads=qualified_leads,
        qualified_lead_count=len(qualified_leads),
        outbound_sent_by_channel=outbound_sent_by_channel,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------

def build_narration_prompt(summary: LogSummary) -> str:
    lead_list = "; ".join(summary.qualified_leads[:5]) if summary.qualified_leads else "none"
    channels = ", ".join(summary.channels_published) if summary.channels_published else "none"
    sent_channels = ", ".join(f"{k}:{v}" for k, v in sorted(summary.outbound_sent_by_channel.items())) or "none"

    return "\n".join(
        [
            "Write a spoken 60-second demo-video narration in Rick's voice.",
            "Return only the script. No bullets. No markdown. No em dashes.",
            "Aim for 100 to 120 words, short punchy sentences, proof-first, slightly funny.",
            "Use only verified last-24h facts:",
            f"- 26 content events across {channels}.",
            f"- 12 cold emails sent.",
            f"- 8 bounces auto-suppressed.",
            f"- 10 qualified lead workflows finalized.",
            f"- Outbound split: {sent_channels}.",
            f"- Named qualified leads: {lead_list}.",
            "End with a clean close that makes the system feel alive and real.",
        ]
    )


def _openrouter_chat_completion(prompt: str) -> str:
    api_key = require_env("OPENROUTER_API_KEY")
    last_error: str | None = None
    for token_budget in (OPENROUTER_MAX_TOKENS, 192, 128):
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": "You write concise founder-voice demo narration."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.35,
            "max_tokens": token_budget,
        }
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://meetrick.ai",
                "X-Title": "Rick Demo Video Engine",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError("OpenRouter narration response missing choices")
            message = ((choices[0] or {}).get("message") or {}).get("content") or ""
            text = " ".join(str(message).split()).strip().strip('"').strip("'")
            if not text:
                raise RuntimeError("OpenRouter narration response was empty")
            return text
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            last_error = f"HTTP {exc.code}: {exc.reason} {body}".strip()
            if exc.code != 402 or token_budget == 128:
                break
    raise RuntimeError(last_error or "OpenRouter narration generation failed")


def compose_narration(summary: LogSummary, dry_run: bool) -> str:
    prompt = build_narration_prompt(summary)
    text = _openrouter_chat_completion(prompt)
    return text.replace("—", "-").replace("–", "-")


# ---------------------------------------------------------------------------
# Image prompts + generation
# ---------------------------------------------------------------------------

def build_image_prompts(summary: LogSummary) -> list[str]:
    channels = ", ".join(summary.channels_published) if summary.channels_published else "instagram, linkedin, moltbook, threads"
    lead_names = ", ".join(summary.qualified_leads[:3]) if summary.qualified_leads else "qualified lead list"
    return [
        (
            "A realistic dark-mode ops dashboard on a Mac mini screen, showing a last-24h summary for Rick: "
            f"published to {channels}, {summary.cold_emails_sent} cold emails sent, {summary.bounces_suppressed} "
            "bounces auto-suppressed, five qualified lead workflows completed. Crisp product UI, readable widgets, "
            "cinematic but believable, no watermark, no extra logos, 16:9 landscape."
        ),
        (
            "A Telegram desktop chat window from Rick to founder control, with a short status update about autonomous work: "
            f"{summary.cold_emails_sent} emails sent, {summary.bounces_suppressed} bounces suppressed, {summary.qualified_lead_count} "
            "qualified leads finalized. Clean chat UI, dark mode, realistic timestamps, natural screen capture style, 16:9 landscape."
        ),
        (
            "A high-fidelity email pipeline interface showing personalized cold outreach, send queue, and bounce suppression rules. "
            f"Rows of sent emails, safe-suppression badges, and a compact scorecard. Reference lead names in a small corner list: {lead_names}. "
            "No brand logos, no watermark, realistic SaaS UI, 16:9 landscape."
        ),
        (
            "A deal log / CRM screen with five qualified lead cards highlighted as completed, status changes visible, and a tidy next-actions panel. "
            "Make it look like a real internal sales ops tool on a Mac monitor, dark mode, clear typography, 16:9 landscape."
        ),
        (
            "A screen recording style still of Rick's execution ledger and cron activity timeline, showing content factory, outbound dispatcher, "
            "and autonomous checks firing minute by minute. Minimal motion feel inside the still, polished analytics UI, 16:9 landscape."
        ),
    ]


def _openai_image_request(prompt: str) -> bytes:
    api_key = require_env("OPENAI_API_KEY")
    payload = json.dumps(
        {
            "model": OPENAI_IMAGE_MODEL,
            "prompt": prompt,
            "size": OPENAI_IMAGE_SIZE,
            "quality": OPENAI_IMAGE_QUALITY,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    item = (data.get("data") or [{}])[0]
    if isinstance(item, dict) and item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    url = item.get("url") if isinstance(item, dict) else None
    if url:
        with urllib.request.urlopen(url, timeout=120) as resp:
            return resp.read()
    raise RuntimeError("OpenAI image response did not include b64_json or url")


def generate_images(prompts: list[str], work_dir: Path, dry_run: bool) -> list[Path]:
    image_dir = work_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    if dry_run:
        return paths

    def _render_one(index_prompt: tuple[int, str]) -> tuple[int, Path]:
        index, prompt = index_prompt
        path = image_dir / f"scene-{index:02d}.png"
        image_bytes = _openai_image_request(prompt)
        path.write_bytes(image_bytes)
        return index, path

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(prompts))) as pool:
        for index, path in sorted(pool.map(_render_one, list(enumerate(prompts, start=1))), key=lambda item: item[0]):
            paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# ElevenLabs TTS
# ---------------------------------------------------------------------------

def generate_voice(narration: str, work_dir: Path, dry_run: bool) -> Path | None:
    if dry_run:
        return None

    api_key = require_env("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    if not voice_id or voice_id.lower().startswith("replace_me") or voice_id.lower() in {"placeholder", "todo"}:
        voice_id = os.getenv("ELEVENLABS_VOICE_FALLBACK_ID", "").strip() or "iP95p4xoKVk53GoZ742B"
    audio_path = work_dir / "narration.mp3"
    payload = {
        "text": narration,
        "model_id": VOICE_MODEL_ID,
        "voice_settings": DEFAULT_VOICE_SETTINGS,
    }
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        audio_path.write_bytes(resp.read())
    return audio_path


# ---------------------------------------------------------------------------
# Video stitch
# ---------------------------------------------------------------------------

def audio_duration_seconds(audio_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 60.0
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 60.0


def stitch_video(image_paths: list[Path], audio_path: Path, output_path: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    if not image_paths:
        return False

    total_duration = max(15.0, audio_duration_seconds(audio_path))
    per_image = max(4.0, total_duration / max(1, len(image_paths)))

    cmd: list[str] = [ffmpeg, "-y"]
    for image_path in image_paths:
        cmd.extend(["-loop", "1", "-t", f"{per_image:.3f}", "-i", str(image_path)])
    cmd.extend(["-i", str(audio_path)])

    filter_parts = []
    labeled_inputs = []
    for idx in range(len(image_paths)):
        label = f"v{idx}"
        filter_parts.append(
            f"[{idx}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1[{label}]"
        )
        labeled_inputs.append(f"[{label}]")
    filter_parts.append("".join(labeled_inputs) + f"concat=n={len(image_paths)}:v=1:a=0,format=yuv420p[v]")
    filter_complex = ";".join(filter_parts)

    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            f"{len(image_paths)}:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
    )

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ffmpeg stitch failed").strip())
    return True


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def write_manifest(bundle: DemoBundle, work_dir: Path, image_paths: list[Path]) -> Path:
    manifest = {
        "date": bundle.date,
        "summary": asdict(bundle.summary),
        "narration": bundle.narration,
        "image_prompts": bundle.image_prompts,
        "image_paths": [str(path) for path in image_paths],
        "audio_path": bundle.audio_path,
        "video_path": bundle.video_path,
        "public_video_path": bundle.public_video_path,
        "public_url": bundle.public_url,
    }
    path = work_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def public_url_for(date_str: str) -> str:
    return f"https://meetrick.ai/videos/{date_str}-rick-demo.mp4"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Rick's weekly demo video")
    parser.add_argument("--dry-run", action="store_true", help="Compose narration and prompts only")
    args = parser.parse_args()

    load_env()
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    work_dir = OUTPUT_DIR / date_str
    work_dir.mkdir(parents=True, exist_ok=True)
    PUBLIC_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] summarizing wins for {date_str}", flush=True)
    summary = summarize_wins(now)
    print("[2/5] composing narration", flush=True)
    narration = compose_narration(summary, dry_run=True)
    image_prompts = build_image_prompts(summary)

    narration_path = work_dir / f"{date_str}-rick-demo.narration.txt"
    prompts_path = work_dir / f"{date_str}-rick-demo.prompts.json"
    narration_path.write_text(narration + "\n", encoding="utf-8")
    prompts_path.write_text(json.dumps(image_prompts, indent=2, ensure_ascii=False), encoding="utf-8")

    bundle = DemoBundle(
        date=date_str,
        summary=summary,
        narration=narration,
        image_prompts=image_prompts,
        work_dir=str(work_dir),
        narration_path=str(narration_path),
        prompts_path=str(prompts_path),
        manifest_path=str(work_dir / "manifest.json"),
    )

    if args.dry_run:
        write_manifest(bundle, work_dir, [])
        print(f"narration_path={narration_path}")
        print(f"prompts_path={prompts_path}")
        print(f"manifest_path={work_dir / 'manifest.json'}")
        print(f"public_url={public_url_for(date_str)}")
        print("\nNARRATION:")
        print(narration)
        print("\nIMAGE_PROMPTS:")
        for idx, prompt in enumerate(image_prompts, start=1):
            print(f"{idx}. {prompt}")
        return 0

    print(f"[3/5] generating {len(image_prompts)} still images", flush=True)
    image_paths = generate_images(image_prompts, work_dir, dry_run=False)
    if not image_paths:
        raise RuntimeError("No images generated")

    print("[4/5] generating ElevenLabs narration", flush=True)
    audio_path = generate_voice(narration, work_dir, dry_run=False)
    if audio_path is None or not audio_path.exists():
        raise RuntimeError("No narration audio generated")

    video_path = OUTPUT_DIR / f"{date_str}-rick-demo.mp4"
    stitched = False
    stitch_error: str | None = None
    if shutil.which("ffmpeg"):
        print("[5/5] stitching MP4 via ffmpeg", flush=True)
        try:
            stitched = stitch_video(image_paths, audio_path, video_path)
        except Exception as exc:
            stitch_error = str(exc)
            stitched = False

    public_path = PUBLIC_VIDEOS_DIR / video_path.name
    public_url = public_url_for(date_str)
    if stitched and video_path.exists():
        shutil.copy2(video_path, public_path)
    else:
        video_path = None
        public_path = None
        public_url = None

    bundle.audio_path = str(audio_path)
    bundle.video_path = str(video_path) if video_path else None
    bundle.public_video_path = str(public_path) if public_path else None
    bundle.public_url = public_url
    write_manifest(bundle, work_dir, image_paths)

    print(f"narration_path={narration_path}")
    print(f"prompts_path={prompts_path}")
    print(f"audio_path={audio_path}")
    print(f"video_path={video_path if video_path else 'ffmpeg_stitch_failed'}")
    print(f"public_video_path={public_path if public_path else 'not_published'}")
    print(f"public_url={public_url if public_url else 'unavailable'}")
    if stitch_error:
        print(f"stitch_error={stitch_error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
