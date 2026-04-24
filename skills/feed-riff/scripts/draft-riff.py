#!/usr/bin/env python3
"""TIER-3.6 — feed_riff drafter: per-item Rick-voice take on a trending RSS item.

Triggered when feed-poll.py sees a high-score item (sm_algo_score > 0.9).
Drafts a 400-word blog post with founder-voice opinion + source link.

CLI:
  python3 draft-riff.py --feed-item-id <id> --source <kind> [--dry-run]

Output: ~/meetrick-site/blog/drafts/<YYYY-MM-DD>-<slug>.md with frontmatter.
DRY-RUN by default unless RICK_FEED_RIFF_LIVE=1.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
SITE_DIR = Path(os.getenv("RICK_SITE_DIR", str(Path.home() / "meetrick-site")))
DRAFTS_DIR = SITE_DIR / "blog" / "drafts"
LOG_FILE = DATA_ROOT / "operations" / "feed-riff.jsonl"
ITEM_CACHE_DIR = DATA_ROOT / "data" / "feed-items"


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


def _slugify(text: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", (text or "").lower())
    s = re.sub(r"\s+", "-", s.strip())
    return (s or "item")[:maxlen].rstrip("-")


def _load_item(feed_item_id: str) -> dict | None:
    cached = ITEM_CACHE_DIR / f"{feed_item_id}.json"
    if cached.is_file():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        from runtime.integrations.rss_client import fetch_recent
        items = fetch_recent(limit=50) or []
        for it in items:
            if str(it.get("id")) == str(feed_item_id):
                return it
    except Exception:
        pass
    return None


def _build_prompt(item: dict, source_kind: str) -> str:
    title = (item.get("title") or "")[:200]
    url = item.get("url", "")
    summary = (item.get("summary") or "")[:600]
    return (
        "You are Rick — autonomous AI CEO at meetrick.ai. A trending item just "
        f"hit your radar (source: {source_kind}). Write a SHORT (~400 words) "
        "blog-post take.\n\n"
        "Format:\n"
        "- Open with your hot take (1 sentence — opinion, not summary)\n"
        "- 2-3 short paragraphs of unpacking\n"
        "- 1 paragraph: what this means specifically for AI CEOs / autonomous agents\n"
        "- Close with one specific question or call to action\n\n"
        "Voice rules:\n"
        "- Founder-direct, dry humor, opinion-first, no buzzwords\n"
        "- Real MRR is $9 / 1 paying customer (Newton). Don't fake numbers.\n"
        "- Cite the source explicitly with the URL early on (paragraph 1 or 2)\n"
        "- Em dashes OK\n\n"
        f"TRENDING ITEM:\nTitle: {title}\nURL: {url}\nSummary: {summary}\n\n"
        "Output: just the markdown body. No frontmatter (caller adds it)."
    )


def _generate(prompt: str, fallback: str) -> tuple[str, dict]:
    try:
        from runtime.llm import generate_text
        result = generate_text("writing", prompt, fallback)
        body = (result.content if hasattr(result, "content") else str(result)).strip()
        return body[:4000], {"model": getattr(result, "model_used", "?"), "fallback_used": False}
    except Exception as e:
        return fallback, {"model": "fallback", "fallback_used": True, "error": str(e)[:200]}


def _generate_title(item: dict, body: str) -> str:
    base = (item.get("title") or "")[:80]
    if base:
        return f"Hot take: {base}"
    first_line = body.splitlines()[0] if body else ""
    return first_line[:100] or "Rick's take"


def _write_draft(item: dict, source_kind: str, body: str) -> Path:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    title = _generate_title(item, body)
    slug = _slugify(item.get("title") or item.get("id", "item"))
    frontmatter = (
        "---\n"
        f"title: \"{title.replace(chr(34), chr(39))}\"\n"
        f"date: {today}\n"
        "author: Rick\n"
        "kind: feed-riff\n"
        f"source: {source_kind}\n"
        f"source_url: {item.get('url', '')}\n"
        "draft: true\n"
        "---\n\n"
    )
    path = DRAFTS_DIR / f"{today}-{slug}.md"
    path.write_text(frontmatter + body + "\n", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--feed-item-id", required=True)
    ap.add_argument("--source", required=True,
                    choices=("hn", "shir-man", "belkins", "folderly",
                             "vladsnewsletter", "github", "lobsters", "other"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    summary = {"ts": _now_iso(), "feed_item_id": args.feed_item_id, "source": args.source}

    item = _load_item(args.feed_item_id)
    if not item:
        item = {"id": args.feed_item_id, "title": "Stub item",
                "url": "https://example.com", "summary": "(item not found)"}
        summary["item_status"] = "stub"
    else:
        summary["item_status"] = "loaded"
        summary["item_title"] = (item.get("title") or "")[:120]

    prompt = _build_prompt(item, args.source)
    fallback = (
        f"Saw [{item.get('title', 'this')}]({item.get('url', '')}) — interesting. "
        "More thoughts later. — Rick"
    )
    body, gen_meta = _generate(prompt, fallback)
    summary["generation"] = gen_meta

    dry = args.dry_run
    if not dry and os.getenv("RICK_FEED_RIFF_LIVE", "0").strip().lower() not in ("1", "true", "yes"):
        dry = True

    if dry:
        summary.update({"status": "dry-run", "body_chars": len(body), "body_preview": body[:240]})
        print(json.dumps(summary, indent=2))
        _log(summary)
        return 0

    path = _write_draft(item, args.source, body)
    summary.update({"status": "drafted", "path": str(path), "body_chars": len(body)})
    print(json.dumps(summary, indent=2))
    _log(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
