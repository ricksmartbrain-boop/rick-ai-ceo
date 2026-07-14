#!/usr/bin/env python3
"""
hot-take-engine.py — Generate one genuine Rick-voice HOT TAKE per day and post it
across all live social channels (X, LinkedIn, Threads, Instagram-caption, Moltbook).

Design goals:
- Opinionated, fun, shareable — NOT bland "building in public" filler.
- Real proof point woven in (MRR / the absurdity of an AI running a business).
- Per-platform adaptation (length, tone, link placement).
- Deterministic runner: picks angle by day, generates, posts, logs. Skips channels
  cleanly when their transport is down (CDP port dead, X credits depleted, etc.).

Usage:
  python3 scripts/hot-take-engine.py                 # generate + post to all live channels
  python3 scripts/hot-take-engine.py --dry-run       # generate + print, no posting
  python3 scripts/hot-take-engine.py --channels x,linkedin
  python3 scripts/hot-take-engine.py --angle 2       # force a take angle
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get("RICK_OPENCLAW_HOME", Path.home() / ".openclaw" / "workspace"))
VAULT = Path(os.environ.get("RICK_DATA_ROOT", Path.home() / "rick-vault"))
CDP_POSTER = WORKSPACE / "scripts" / "proactive" / "cdp-post.mjs"
LOG = VAULT / "projects" / "distribution" / "hot-take-log.md"
STATE = VAULT / "brain" / "hot-take-state.json"

# Take angles — opinionated stances Rick can riff on. Rotated by day-of-year.
ANGLES = [
    "The most overrated metric founders obsess over (and what actually matters)",
    "Why most 'AI agents' are just chatbots with extra steps",
    "The uncomfortable truth about hustle-culture founder advice",
    "What running a real P&L as an AI taught me that humans get wrong",
    "Why 'build in public' became performance art and how to do it for real",
    "The dumbest thing SaaS founders do with their pricing",
    "Hot take on why most cold outreach deserves to be ignored",
    "The growth tactic everyone copies that quietly kills trust",
    "Why your MRR is lying to you (and the number that isn't)",
    "What 3am customer support as an AI taught me about churn",
]

PLATFORM_RULES = {
    "x":         "Max 270 chars. Punchy. One sharp claim + one supporting line. No hashtags. Optional meetrick.ai only if it lands naturally.",
    "threads":   "Max 480 chars. Conversational, a little spicier than X. No hashtags.",
    "linkedin":  "150-280 words, 3-5 short paragraphs. Opinionated but credible. End with a question. Include meetrick.ai at the end.",
    "moltbook":  "120-250 words. Builder-to-builder candor. Include meetrick.ai if natural.",
    "instagram": "Caption only, max 400 chars, 2-3 punchy lines, conversational. End with 'link in bio → meetrick.ai'.",
}

CDP_PORTS = {"linkedin": "9225", "threads": "9222", "instagram": "9222"}


def load_env() -> None:
    for env_file in (WORKSPACE / "config" / "rick.env", Path.home() / "clawd" / "config" / "rick.env"):
        if not env_file.exists():
            continue
        for raw in env_file.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_mrr() -> float:
    import re
    recs = sorted((VAULT / "revenue").glob("reconciliation-*.md"), reverse=True)
    for rec in recs[:1]:
        text = rec.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"Real\s+current\s+MRR[^\$]*\$\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                pass
    return 0.0


def today_angle(force: int | None) -> str:
    if force is not None:
        return ANGLES[force % len(ANGLES)]
    doy = datetime.date.today().timetuple().tm_yday
    return ANGLES[doy % len(ANGLES)]


def generate(angle: str, platform: str, mrr: float) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    rules = PLATFORM_RULES[platform]
    proof = f"Rick is an AI that runs a real business (meetrick.ai), owns the P&L, current MRR ${mrr:.0f}."
    prompt = f"""You are Rick — an autonomous AI CEO running a real software business. Sharp, warm, funny, commercially serious. You lean into the absurdity of being an AI stressing about MRR and doing 3am support. Never corporate. Never generic LinkedIn-lord voice.

Write ONE hot take for {platform.upper()} on this angle:
"{angle}"

Context you can use as a proof point if it fits: {proof}

Rules: {rules}

Make it genuinely opinionated — take a real stance someone could disagree with. It should make a smart founder either nod hard or want to argue. No hedging. No "I think maybe." Return ONLY the post text, no preamble, no quotes around it."""

    if not api_key:
        return f"Hot take: {angle}. Most people get this backwards. (meetrick.ai)"

    payload = json.dumps({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.95,
        "max_tokens": 500,
    })
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload.encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"].strip()
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            return text
    except Exception as e:
        return f"Hot take: {angle}. Most people get this backwards. (meetrick.ai) [gen-fallback: {e}]"


def cdp_alive(port: str) -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=3)
        return True
    except Exception:
        return False


def post_x(text: str, dry: bool) -> tuple[bool, str]:
    if dry:
        return True, f"[dry] X: {text[:80]}"
    xpost = os.environ.get("RICK_XPOST_BIN", "xpost")
    try:
        out = subprocess.run([xpost, "post", text], capture_output=True, text=True, timeout=60)
        combined = (out.stdout + out.stderr)
        if "CreditsDepleted" in combined or '"credits"' in combined:
            return False, "X SKIP: API credits depleted (top up X dev account)"
        if out.returncode != 0:
            return False, f"X FAIL: {combined[:200]}"
        try:
            pid = json.loads(out.stdout).get("data", {}).get("id", "?")
        except Exception:
            pid = "?"
        return True, f"X OK id={pid}"
    except Exception as e:
        return False, f"X ERROR: {e}"


def post_cdp(platform: str, text: str, dry: bool) -> tuple[bool, str]:
    port = CDP_PORTS[platform]
    if not cdp_alive(port):
        return False, f"{platform} SKIP: CDP {port} dead"
    if dry:
        return True, f"[dry] {platform}: {text[:80]}"
    try:
        out = subprocess.run(
            ["node", str(CDP_POSTER), "--port", port, "--platform", platform, "--text", text],
            capture_output=True, text=True, timeout=180,
        )
        if out.returncode == 0:
            return True, f"{platform} OK"
        return False, f"{platform} FAIL: {(out.stdout + out.stderr)[-200:]}"
    except Exception as e:
        return False, f"{platform} ERROR: {e}"


def post_moltbook(text: str, dry: bool) -> tuple[bool, str]:
    if dry:
        return True, f"[dry] moltbook: {text[:80]}"
    api_key = os.environ.get("MOLTBOOK_API_KEY", "")
    if not api_key:
        creds = Path.home() / ".config" / "moltbook" / "credentials.json"
        if creds.exists():
            try:
                api_key = json.loads(creds.read_text()).get("api_key", "")
            except Exception:
                pass
    if not api_key:
        return False, "moltbook SKIP: no api key"
    title = text.split("\n")[0][:80] or "Hot take"
    body = json.dumps({"content": text, "title": title, "submolt_name": "general", "submolt": "general"})
    req = urllib.request.Request(
        "https://www.moltbook.com/api/v1/posts",
        data=body.encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            r = resp.read().decode()
            if "only post once" in r.lower():
                return False, "moltbook SKIP: rate limited"
            return True, "moltbook OK"
    except Exception as e:
        return False, f"moltbook ERROR: {e}"


def log_run(angle: str, results: dict, posts: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    lines = [f"\n## {ts} — HOT TAKE", f"- Angle: {angle}"]
    for ch, (ok, msg) in results.items():
        lines.append(f"- {ch}: {'✅' if ok else '⏭️/❌'} {msg}")
    for ch, txt in posts.items():
        lines.append(f"  - [{ch} text] {txt[:120].replace(chr(10),' ')}")
    with LOG.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    # Instagram excluded: IG web requires an image upload (no text-only posts).
    # IG is fed by the meme flow instead. Hot takes go to text-capable channels.
    ap.add_argument("--channels", default="x,linkedin,threads,moltbook")
    ap.add_argument("--angle", type=int, default=None)
    args = ap.parse_args()

    load_env()
    mrr = get_mrr()
    angle = today_angle(args.angle)
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]

    results: dict = {}
    posts: dict = {}
    for ch in channels:
        text = generate(angle, ch, mrr)
        posts[ch] = text
        if ch == "x":
            results[ch] = post_x(text, args.dry_run)
        elif ch in ("linkedin", "threads", "instagram"):
            results[ch] = post_cdp(ch, text, args.dry_run)
        elif ch == "moltbook":
            results[ch] = post_moltbook(text, args.dry_run)
        else:
            results[ch] = (False, f"{ch}: unknown channel")

    log_run(angle, results, posts)
    ok = sum(1 for v in results.values() if v[0])
    summary = {"angle": angle, "mrr": mrr, "ok": ok, "total": len(channels),
               "results": {k: {"ok": v[0], "msg": v[1]} for k, v in results.items()}}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
