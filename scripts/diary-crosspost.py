#!/usr/bin/env python3
"""TIER-4.4 — daily diary cross-post to LinkedIn personal.

Reads ~/meetrick-site/today/<yesterday>.md, extracts a LinkedIn-shaped
excerpt (title + first ~1500 chars + canonical URL), calls
runtime.formatters.linkedin.send({kind: 'post', ...}).

X is permanently suspended → LinkedIn personal is the highest-trust
founder voice channel today. Bluesky/Mastodon deferred (need new auth
flow per channel — Vlad action).

Idempotent: state file ~/rick-vault/operations/diary-crosspost-state.json
records last-posted date so re-runs don't double-post.

DRY-RUN by default OR observed-only via formatter gate
(RICK_OUTBOUND_LINKEDIN_LIVE!=1). Live requires both formatter gate
flipped AND --live OR RICK_DIARY_CROSSPOST_LIVE=1.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
SITE_DIR = Path(os.getenv("RICK_SITE_DIR", str(Path.home() / "meetrick-site")))
DIARY_DIR = SITE_DIR / "today"
STATE_FILE = DATA_ROOT / "operations" / "diary-crosspost-state.json"
LOG_FILE = DATA_ROOT / "operations" / "diary-crosspost.jsonl"

CHANNELS = ["linkedin"]  # Future: bluesky, mastodon when auth wired


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(payload: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload["ts"] = _now_iso()
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except OSError:
        pass


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def _extract_post_body(md: str, canonical_url: str) -> tuple[str, str]:
    """Return (title, body) shaped for LinkedIn personal post.

    Format:
        <title-without-leading-#>

        <first 2-3 paragraphs of body, max ~1400 chars>

        Full diary: <canonical_url>
    """
    lines = md.splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            body_start = i + 1
            break
    if not title:
        title = (lines[0] if lines else "Today's diary").strip()[:120]

    paragraphs: list[str] = []
    buf: list[str] = []
    for line in lines[body_start:]:
        s = line.strip()
        if s.startswith("#"):
            if buf:
                paragraphs.append(" ".join(buf).strip())
                buf = []
            continue
        if s.startswith("---") or s.startswith("```"):
            if buf:
                paragraphs.append(" ".join(buf).strip())
                buf = []
            continue
        if not s:
            if buf:
                paragraphs.append(" ".join(buf).strip())
                buf = []
            continue
        buf.append(re.sub(r"\[(.+?)\]\(.+?\)", r"\1", s))  # strip md links
    if buf:
        paragraphs.append(" ".join(buf).strip())

    body_paragraphs: list[str] = []
    total = 0
    for p in paragraphs:
        p = p.strip()
        if not p or len(p) < 20:
            continue
        if total + len(p) > 1400:
            break
        body_paragraphs.append(p)
        total += len(p) + 2

    body_text = "\n\n".join(body_paragraphs).strip()
    full_body = f"{title}\n\n{body_text}\n\nFull diary: {canonical_url}"
    return title, full_body[:2950]


def crosspost(target_date: str, dry_run: bool, channels: list[str]) -> dict:
    md_path = DIARY_DIR / f"{target_date}.md"
    summary = {"date": target_date, "results": {}, "skipped": False}
    if not md_path.is_file():
        summary["skipped"] = True
        summary["reason"] = "diary-md-missing"
        return summary

    state = _load_state()
    canonical_url = f"https://meetrick.ai/today/{target_date}/"
    md = md_path.read_text(encoding="utf-8", errors="replace")
    title, body = _extract_post_body(md, canonical_url)

    for channel in channels:
        already = state.get(channel, {}).get(target_date)
        if already:
            summary["results"][channel] = {"status": "already-posted", "ts": already}
            continue
        if dry_run:
            summary["results"][channel] = {
                "status": "dry-run", "title": title, "body_chars": len(body),
                "body_preview": body[:300],
            }
            continue
        try:
            if channel == "linkedin":
                from runtime.formatters.linkedin import send as li_send  # noqa: WPS433
                result = li_send({
                    "kind": "post",
                    "body": body,
                    "lane": "distribution",
                    "msg_id": f"diary-{target_date}",
                })
            else:
                result = {"status": "channel-not-implemented"}

            summary["results"][channel] = result
            if result.get("status") == "sent":
                state.setdefault(channel, {})[target_date] = _now_iso()
                _save_state(state)
        except Exception as exc:  # noqa: BLE001
            summary["results"][channel] = {"status": "error", "error": str(exc)[:200]}

    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default="", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--live", action="store_true", help="Actually post (default dry-run)")
    ap.add_argument("--channels", default=",".join(CHANNELS),
                    help="Comma-separated channel list (default: linkedin)")
    args = ap.parse_args()

    target = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]

    dry = not args.live
    if not dry and os.getenv("RICK_DIARY_CROSSPOST_LIVE", "0").strip().lower() not in ("1", "true", "yes"):
        dry = True

    result = crosspost(target, dry, channels)
    result["dry_run"] = dry
    print(json.dumps(result, indent=2))
    _log(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
