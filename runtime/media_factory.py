"""Media generation layer for content_factory output.

Threads + Instagram formatters reject payloads without media (per
runtime/formatters/{threads,instagram}.py). content_factory generates
text angles every 4h. media_factory bridges the gap by attaching a
local image/video file path to the payload so those channels can ship.

Provider chain (try in order, fall through on failure):
  1. Memelord image API — already wired in scripts/memelord-pipeline.py;
     re-implemented here so content_factory can call without shelling out.
  2. gpt-image-2 — TODO: runtime/llm.py has no image route as of 2026-04-25.
     Wire when an image route is added; for now, stub returns None.

Failure mode: returns the payload unchanged. The dispatcher's existing
PermanentError + retry/backoff handles downstream — no crash, content
just doesn't ship until next factory tick.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
MEDIA_ROOT = DATA_ROOT / "media"
LOG_FILE = DATA_ROOT / "operations" / "media-factory.jsonl"

# Channels that REQUIRE media (formatter raises PermanentError without it).
# moltbook + blog + x_twitter are text-only; bypass entirely.
MEDIA_REQUIRED = {"instagram", "threads"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(entry: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now_iso(), **entry}, sort_keys=True) + "\n")
    except OSError:
        pass


def _payload_text(payload: dict) -> str:
    for key in ("body", "caption", "content", "text", "message"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _build_prompt(text: str, angle: Optional[str]) -> str:
    snippet = text[:80].replace("\n", " ").strip()
    angle_tag = f" Tone: {angle}." if angle else ""
    base = f"Minimalist tech illustration matching: {snippet}.{angle_tag} Clean modern style, no text overlay."
    return base[:200]


def _cache_path(angle: Optional[str], prompt: str) -> Path:
    h = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:16]
    safe_angle = (angle or "untagged").replace("/", "_")[:32]
    date = datetime.now().strftime("%Y-%m-%d")
    return MEDIA_ROOT / date / f"{safe_angle}-{h}.webp"


def _detect_image_ext(raw: bytes, hint: str | None = None) -> str:
    low = (hint or "").lower()
    if "webp" in low:
        return "webp"
    if "png" in low:
        return "png"
    if "jpeg" in low or "jpg" in low:
        return "jpg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WEBP":
        return "webp"
    if raw.startswith(b"\xff\xd8"):
        return "jpg"
    return "png"


def _write_image_bytes(out_path: Path, raw: bytes, *, hint: str | None = None) -> Path:
    ext = _detect_image_ext(raw, hint=hint)
    final_path = out_path.with_suffix(f".{ext}") if out_path.suffix.lower() != f".{ext}" else out_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(raw)
    if final_path != out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw)
    return final_path


def _memelord_image(prompt: str, out_path: Path,
                    channel: str = "?", angle: str = "?") -> Optional[str]:
    """Generate one image via Memelord. Returns local file path or None.

    Replicates the contract used by scripts/memelord-pipeline.py:343-397.
    Same Bearer auth, same JSON body shape, same response handling.
    channel + angle are forwarded into log entries so failure forensics
    can identify which content_factory call site produced the error.
    """
    api_key = os.getenv("MEMELORD_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import requests  # local import — not all environments have it
    except ImportError:
        return None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "prompt": prompt,
        "count": 1,
        "category": "trending",
        "include_nsfw": False,
    }
    started = time.monotonic()
    last_err = None
    # 90s per attempt: Memelord image gen runs an LLM + image model server-side
    # and typically takes 30-60s. 20s was too aggressive (2026-04-26: every
    # call timing out at exactly 20s in media-factory.jsonl, zero successes).
    # 90s matches the working contract in scripts/memelord-pipeline.py:360.
    # 1 retry on transient 5xx; no retry on 4xx (likely a bad prompt).
    for attempt in (1, 2):
        try:
            resp = requests.post(
                "https://www.memelord.com/api/v1/ai-meme",
                headers=headers, json=body, timeout=90,
            )
            if 500 <= resp.status_code < 600 and attempt == 1:
                last_err = f"http {resp.status_code}"
                time.sleep(2)
                continue
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                # Don't log full data — may echo prompt back containing
                # internal lessons text that we don't need round-tripped.
                _log({
                    "channel": channel, "angle": angle, "provider": "memelord",
                    "status": "rejected", "latency_ms": int((time.monotonic() - started) * 1000),
                    "error": str(data.get("error") or "success=false")[:200],
                })
                return None
            results = data.get("results") or []
            if not results:
                return None
            url = results[0].get("url")
            if not url:
                return None
            img = requests.get(url, timeout=20)
            img.raise_for_status()
            written = _write_image_bytes(out_path, img.content, hint=(img.headers.get("content-type") or url))
            _log({
                "channel": channel, "angle": angle, "provider": "memelord",
                "status": "generated", "latency_ms": int((time.monotonic() - started) * 1000),
                "file_path": str(written),
                "bytes": len(img.content),
            })
            return str(written)
        except Exception as exc:  # network, parse, http error
            last_err = str(exc)[:200]
            if attempt == 1:
                time.sleep(2)
                continue
            _log({
                "channel": channel, "angle": angle, "provider": "memelord",
                "status": "error", "latency_ms": int((time.monotonic() - started) * 1000),
                "error": last_err,
            })
            return None
    return None


def _gpt_image_2(prompt: str, out_path: Path,
                 channel: str = "?", angle: str = "?") -> Optional[str]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import requests  # local import — not all environments have it
    except ImportError:
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "gpt-image-2",
        "prompt": prompt,
        "size": "1024x1024",
        "n": 1,
        # Note: gpt-image-2 returns b64_json by default — `response_format` is
        # rejected with HTTP 400 (only valid for legacy dall-e-*). Confirmed
        # via direct API test 2026-04-27 after the silent-fail bug was caught.
    }
    started = time.monotonic()
    last_err = None
    for attempt in (1, 2):
        resp = None
        try:
            resp = requests.post(
                "https://api.openai.com/v1/images/generations",
                headers=headers, json=body, timeout=60,
            )
            if 500 <= resp.status_code < 600 and attempt == 1:
                last_err = f"http {resp.status_code}"
                time.sleep(2)
                continue
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            items = data.get("data") or []
            item = items[0] if items else {}
            b64 = item.get("b64_json") or data.get("b64_json")
            if not b64:
                last_err = "missing b64_json in response"
                _log({
                    "channel": channel, "angle": angle, "provider": "gpt-image-2",
                    "status": "error", "latency_ms": int((time.monotonic() - started) * 1000),
                    "error": last_err,
                })
                return None
            raw = base64.b64decode(b64)
            ext_hint = item.get("output_format") or data.get("output_format")
            written = _write_image_bytes(out_path, raw, hint=ext_hint)
            _log({
                "channel": channel, "angle": angle, "provider": "gpt-image-2",
                "status": "generated", "latency_ms": int((time.monotonic() - started) * 1000),
                "file_path": str(written),
                "bytes": len(raw),
                "output_format": _detect_image_ext(raw, hint=ext_hint),
            })
            return str(written)
        except Exception as exc:  # network, parse, http error
            last_err = str(exc)[:200]
            if resp is not None and resp.status_code >= 500 and attempt == 1:
                time.sleep(2)
                continue
            _log({
                "channel": channel, "angle": angle, "provider": "gpt-image-2",
                "status": "error", "latency_ms": int((time.monotonic() - started) * 1000),
                "error": last_err,
            })
            return None
    return None


def attach_media(channel: str, payload: dict, angle: Optional[str] = None) -> dict:
    """Mutate payload to include media for channels that require it.

    Returns the same payload dict (modified in place + returned).
    No-op for channels that don't need media (moltbook, blog, x_twitter).
    Failure is silent: payload is returned unchanged so caller can decide
    whether to skip or queue text-only — formatter will raise PermanentError
    on its own contract violation if the payload is incomplete.
    """
    if not channel or channel not in MEDIA_REQUIRED:
        return payload
    # Already has media? leave it.
    if payload.get("image_path") or payload.get("video_path"):
        return payload
    text = _payload_text(payload)
    if not text:
        _log({
            "channel": channel, "angle": angle or "", "provider": "none",
            "status": "no_text_in_payload",
            "error": "payload had no body/caption/content/text/message field",
        })
        return payload
    prompt = _build_prompt(text, angle)
    cache = _cache_path(angle, prompt)
    started = time.monotonic()
    if cache.exists():
        payload["image_path"] = str(cache)
        _log({
            "channel": channel, "angle": angle or "", "provider": "cache",
            "status": "hit", "latency_ms": int((time.monotonic() - started) * 1000),
            "file_path": str(cache),
        })
        return payload
    memelord_key = os.getenv("MEMELORD_API_KEY", "").strip()
    if angle == "meme" and memelord_key:
        provider_chain = (("memelord", _memelord_image), ("gpt-image-2", _gpt_image_2))
    else:
        provider_chain = (("gpt-image-2", _gpt_image_2),)
        if memelord_key:
            provider_chain += (("memelord", _memelord_image),)

    for provider, fn in provider_chain:
        result = fn(prompt, cache, channel=channel, angle=(angle or ""))
        if result:
            payload["image_path"] = result
            _log({
                "channel": channel, "angle": angle or "", "provider": provider,
                "status": "generated", "latency_ms": int((time.monotonic() - started) * 1000),
                "file_path": result,
            })
            return payload
    _log({
        "channel": channel, "angle": angle or "", "provider": "none",
        "status": "fallthrough", "latency_ms": int((time.monotonic() - started) * 1000),
        "error": "no provider produced media; payload returned text-only",
    })
    return payload
