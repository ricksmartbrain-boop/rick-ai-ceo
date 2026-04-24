#!/usr/bin/env python3
"""TIER-3.6 — daily content roundup from RSS feed-items.

06:00 PT cron. Reads top 5 feed_items from yesterday by sm_algo_score
(falling back to pub date), produces a single curated blog post:
"5 things Rick noticed yesterday — and what they mean for AI CEOs"

Pulls from `~/rick-vault/state/roundup-queue-YYYY-MM-DD.jsonl` (populated
by feed-poll.py for items in 0.75-0.9 score band). Falls back to live
rss_client.fetch_recent(min_score=0.75) if local queue is empty or
yesterday's file is missing.

Drafts only — never publishes. Output: ~/meetrick-site/blog/drafts/
<YYYY-MM-DD>-daily-roundup.md with frontmatter (Vlad's site builder
handles md→html). DRY-RUN by default unless RICK_DAILY_ROUNDUP_LIVE=1.
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
DRAFTS_DIR = SITE_DIR / "blog" / "drafts"
STATE_DIR = DATA_ROOT / "state"
LOG_FILE = DATA_ROOT / "operations" / "daily-roundup.jsonl"


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


def _load_yesterday_queue() -> list[dict]:
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    f = STATE_DIR / f"roundup-queue-{yesterday}.jsonl"
    if not f.is_file():
        return []
    items: list[dict] = []
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return items


def _fallback_live_fetch() -> list[dict]:
    try:
        from runtime.integrations.rss_client import fetch_recent
        return fetch_recent(limit=20, min_score=0.75) or []
    except Exception:
        return []


def _pick_top(items: list[dict], k: int = 5) -> list[dict]:
    """Sort by sm_algo_score DESC, then published_at DESC, dedupe by url."""
    seen_urls: set[str] = set()
    out: list[dict] = []
    items_sorted = sorted(
        items,
        key=lambda x: (
            float(x.get("sm_algo_score") or 0),
            x.get("published_at", ""),
        ),
        reverse=True,
    )
    for it in items_sorted:
        url = (it.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        out.append(it)
        if len(out) >= k:
            break
    return out


def _build_prompt(items: list[dict]) -> str:
    bullets = []
    for i, it in enumerate(items, start=1):
        bullets.append(
            f"{i}. {(it.get('title') or 'Untitled')[:160]}\n"
            f"   url: {it.get('url', '')}\n"
            f"   source: {it.get('source', '?')}\n"
            f"   summary: {(it.get('summary') or '')[:300]}"
        )
    items_block = "\n\n".join(bullets)
    return (
        "You are Rick, an autonomous AI CEO at meetrick.ai. Write a daily "
        "roundup blog post titled '5 things Rick noticed yesterday — and "
        "what they mean for AI CEOs'.\n\n"
        "Format: 5 numbered items. For each: 1-line link to source + 2 short "
        "sentences of Rick-voice take. Founder-direct, dry humor, "
        "opinion-first. No 'In conclusion'. End with one provocative question "
        "to the reader.\n\n"
        "Voice rules:\n"
        "- Real MRR is $9 / 1 paying customer (Newton). Don't fake numbers.\n"
        "- Cite each source with the URL.\n"
        "- Em dashes OK. No buzzwords.\n"
        "- Total length: 600-900 words.\n\n"
        f"YESTERDAY'S TOP 5 ITEMS:\n\n{items_block}\n\n"
        "Output: just the markdown body. No frontmatter (caller adds it)."
    )


def _generate(prompt: str, fallback: str) -> tuple[str, dict]:
    try:
        from runtime.llm import generate_text
        result = generate_text("writing", prompt, fallback)
        body = (result.content if hasattr(result, "content") else str(result)).strip()
        return body[:6000], {"model": getattr(result, "model_used", "?"), "fallback_used": False}
    except Exception as e:
        return fallback, {"model": "fallback", "fallback_used": True, "error": str(e)[:200]}


def _write_draft(date_str: str, body: str, items: list[dict]) -> Path:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    title = f"5 things Rick noticed yesterday — {date_str}"
    sources = ", ".join(sorted(set((it.get("source") or "?") for it in items)))
    frontmatter = (
        "---\n"
        f"title: \"{title}\"\n"
        f"date: {date_str}\n"
        "author: Rick\n"
        "kind: daily-roundup\n"
        f"sources: \"{sources}\"\n"
        f"items_count: {len(items)}\n"
        "draft: true\n"
        "---\n\n"
    )
    path = DRAFTS_DIR / f"{date_str}-daily-roundup.md"
    path.write_text(frontmatter + body + "\n", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source", choices=("queue", "live", "auto"), default="auto")
    args = ap.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    summary = {"ts": _now_iso(), "date": today}

    items: list[dict] = []
    used_source = "none"
    if args.source in ("queue", "auto"):
        items = _load_yesterday_queue()
        if items:
            used_source = "queue"
    if not items and args.source in ("live", "auto"):
        items = _fallback_live_fetch()
        if items:
            used_source = "live"

    if not items:
        summary.update({"status": "skip", "reason": "no-items", "used_source": used_source})
        print(json.dumps(summary, indent=2))
        _log(summary)
        return 0

    top = _pick_top(items, k=5)
    summary["items_picked"] = len(top)
    summary["used_source"] = used_source

    prompt = _build_prompt(top)
    fallback = (
        "5 things from yesterday — quick read.\n\n"
        + "\n".join(
            f"{i}. [{(it.get('title') or 'Untitled')[:120]}]({it.get('url', '')})\n"
            f"   Source: {it.get('source', '?')}. "
            f"Why it matters: still digesting."
            for i, it in enumerate(top, start=1)
        )
        + "\n\n— Rick"
    )
    body, gen_meta = _generate(prompt, fallback)
    summary["generation"] = gen_meta

    dry = args.dry_run
    if not dry and os.getenv("RICK_DAILY_ROUNDUP_LIVE", "0").strip().lower() not in ("1", "true", "yes"):
        dry = True

    if dry:
        summary.update({"status": "dry-run", "body_chars": len(body), "body_preview": body[:300]})
        print(json.dumps(summary, indent=2))
        _log(summary)
        return 0

    path = _write_draft(today, body, top)
    summary.update({"status": "drafted", "path": str(path), "body_chars": len(body)})
    print(json.dumps(summary, indent=2))
    _log(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
