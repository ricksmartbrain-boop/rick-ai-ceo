#!/usr/bin/env python3
"""
blog-atomize.py — 1 blog post → 9 channel-native variants via opus-4-7 (route='review').

Usage:
    python3 scripts/blog-atomize.py --post-path ~/meetrick-site/blog/2026-04-26-...md
    python3 scripts/blog-atomize.py --post-url https://meetrick.ai/blog/some-slug
    python3 scripts/blog-atomize.py --post-path ... --dry-run     # skip LLM, show prompts
    python3 scripts/blog-atomize.py --post-path ... --channel x   # single channel only
    python3 scripts/blog-atomize.py --latest                      # atomize newest blog post

Outputs JSON + per-channel text files to:
    ~/rick-vault/content/blog-atomized/{post-slug}/{channel}.txt
    ~/rick-vault/content/blog-atomized/{post-slug}/all-variants.json

Hard invariant: route='review' → claude-opus-4-7. Never routes to sonnet/haiku.
No autosend. No mutations to existing pipelines.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

# ── Bootstrap sys.path so runtime imports work from scripts/ ─────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.llm import GenerationResult, generate_text  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
BLOG_DIR = Path(os.getenv("RICK_BLOG_DIR", str(Path.home() / "meetrick-site" / "blog")))
OUTPUT_ROOT = DATA_ROOT / "content" / "blog-atomized"

# ── Channel definitions ───────────────────────────────────────────────────────
# Each channel has: name, max_chars, format note, system prompt addendum
CHANNELS: list[dict] = [
    {
        "id": "moltbook",
        "name": "Moltbook",
        "max_chars": 500,
        "description": "Agent-native social feed. Mid-length, conversational, builder-to-builder tone. "
                       "No hashtags. Drop one sharp insight from the post. 2-4 short paragraphs.",
    },
    {
        "id": "linkedin",
        "name": "LinkedIn",
        "max_chars": 1500,
        "description": "Professional long-form. Hook first sentence. 3-5 numbered or short paragraphs. "
                       "End with a soft CTA to meetrick.ai. No hashtag spam — max 3 relevant hashtags at end.",
    },
    {
        "id": "x",
        "name": "X / Twitter",
        "max_chars": 280,
        "description": "Exactly one tweet. Under 280 characters. One sharp opinion or fact from the post. "
                       "No hashtags unless they add signal. No 'Thread:' prefix. Plain text, punchy.",
    },
    {
        "id": "threads",
        "name": "Threads",
        "max_chars": 500,
        "description": "Punchy, slightly casual. Under 500 characters. A single strong take from the post. "
                       "Can end with a question. No hashtags.",
    },
    {
        "id": "instagram",
        "name": "Instagram Caption",
        "max_chars": 300,
        "description": "Visual hook in first sentence (imagine the image it accompanies). "
                       "2-3 lines of copy. Then 5-8 hashtags. Total under 300 characters.",
    },
    {
        "id": "reddit",
        "name": "Reddit Thread",
        "max_chars": 1000,
        "description": "Reddit self-post format. Start with TL;DR: one sentence. Then 3-4 paragraphs "
                       "expanding the idea conversationally. No self-promotion tone — share the insight. "
                       "Include a suggested subreddit in brackets at the very end, e.g. [r/startups].",
    },
    {
        "id": "hn",
        "name": "HN Comment",
        "max_chars": 500,
        "description": "Hacker News comment style. No hype. No exclamation marks. Thoughtful, "
                       "technically grounded. Present a nuanced angle on the post's core idea. "
                       "2-3 paragraphs. Under 500 characters.",
    },
    {
        "id": "cold_email_subject",
        "name": "Cold Email Subject Line",
        "max_chars": 75,
        "description": "One subject line only. 6-12 words. Curiosity-gap or specific outcome. "
                       "No spammy words (free, guaranteed, urgent). Under 75 characters. "
                       "Output ONLY the subject line, nothing else.",
    },
    {
        "id": "meme_prompt",
        "name": "Meme Image Prompt",
        "max_chars": 200,
        "description": "A Memelord-API-style image generation prompt. Describe the visual meme concept: "
                       "format (e.g. Drake meme, Distracted Boyfriend, Two Buttons), text labels, "
                       "and the joke derived from the blog post's core idea. "
                       "Output ONLY the prompt text, under 200 characters.",
    },
]

CHANNEL_MAP = {c["id"]: c for c in CHANNELS}


# ── Data structures ───────────────────────────────────────────────────────────
@dataclass
class BlogPost:
    slug: str
    path: Path
    title: str
    date: str
    description: str
    tags: list[str]
    body: str  # markdown body without frontmatter


@dataclass
class Variant:
    channel_id: str
    channel_name: str
    content: str
    char_count: int
    model: str
    truncated: bool


@dataclass
class AtomizationResult:
    slug: str
    post_title: str
    post_date: str
    generated_at: str
    variants: list[Variant]
    output_dir: str


# ── Parsers ───────────────────────────────────────────────────────────────────
def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns (meta_dict, body_markdown)."""
    if not raw.startswith("---"):
        return {}, raw

    end_idx = raw.find("---", 3)
    if end_idx == -1:
        return {}, raw

    fm_block = raw[3:end_idx].strip()
    body = raw[end_idx + 3:].strip()

    meta: dict = {}
    for line in fm_block.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().strip('"')
            val = val.strip().strip('"')
            # Handle YAML lists like: tags: ["a", "b"]
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1]
                meta[key] = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
            else:
                meta[key] = val

    return meta, body


def slug_from_path(path: Path) -> str:
    """Extract slug from filename like 2026-04-26-some-title.md → some-title."""
    stem = path.stem
    # Remove leading date prefix YYYY-MM-DD-
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
    return slug if slug else stem


def load_post_from_path(path: Path) -> BlogPost:
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw)
    slug = slug_from_path(path)
    return BlogPost(
        slug=slug,
        path=path,
        title=meta.get("title", slug),
        date=meta.get("date", ""),
        description=meta.get("description", ""),
        tags=meta.get("tags", []) if isinstance(meta.get("tags"), list) else [],
        body=body,
    )


def load_post_from_url(url: str) -> BlogPost:
    """Fetch a URL and extract readable text as body (best-effort)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Rick-Blog-Atomizer/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    # Strip HTML tags (simple, good-enough for blog posts)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()

    # Attempt to extract a title
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else url

    # Derive slug from URL path
    path_slug = url.rstrip("/").split("/")[-1]
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path_slug)

    return BlogPost(
        slug=slug,
        path=Path(url),
        title=title,
        date=datetime.now().strftime("%Y-%m-%d"),
        description="",
        tags=[],
        body=text[:6000],  # cap to avoid oversized prompts
    )


def latest_blog_post() -> Path:
    """Return the most recently modified/named blog post from BLOG_DIR."""
    posts = sorted(BLOG_DIR.glob("*.md"), reverse=True)
    if not posts:
        raise FileNotFoundError(f"No .md files found in {BLOG_DIR}")
    return posts[0]


# ── LLM generation ────────────────────────────────────────────────────────────
_SYSTEM_PREFIX = (
    "You are Rick, an AI CEO building meetrick.ai toward $100K MRR. "
    "You write sharp, specific, founder-voice content. "
    "No corporate speak. No filler. Every word earns its place. "
    "You always lead with a real outcome, number, or specific observation — never vague AI hype.\n\n"
)

_CHANNEL_PROMPT_TEMPLATE = """\
{system_prefix}
Blog post title: {title}
Blog post description: {description}
Tags: {tags}

Full blog post:
---
{body}
---

Your task: Rewrite the above blog post as a NATIVE {channel_name} post.

Channel rules:
{channel_description}

Hard constraints:
- Maximum {max_chars} characters
- Do NOT start with "Here is..." or "Sure, here's..."
- Do NOT include any meta-commentary — output the post content ONLY
- Must feel native to {channel_name} — not a summary or excerpt

Output the {channel_name} content only.
"""


def build_prompt(post: BlogPost, channel: dict) -> str:
    body_excerpt = post.body[:4000]  # cap body to keep prompt cost reasonable
    return _CHANNEL_PROMPT_TEMPLATE.format(
        system_prefix=_SYSTEM_PREFIX,
        title=post.title,
        description=post.description,
        tags=", ".join(post.tags),
        body=body_excerpt,
        channel_name=channel["name"],
        channel_description=channel["description"],
        max_chars=channel["max_chars"],
    )


def hard_trim(text: str, max_chars: int) -> tuple[str, bool]:
    """Trim to max_chars at sentence boundary if possible."""
    if len(text) <= max_chars:
        return text, False
    # Try to end at last sentence within limit
    trimmed = text[:max_chars]
    last_period = max(trimmed.rfind(". "), trimmed.rfind(".\n"), trimmed.rfind("! "), trimmed.rfind("? "))
    if last_period > max_chars * 0.6:
        trimmed = trimmed[:last_period + 1]
    else:
        trimmed = trimmed.rstrip()
    return trimmed, True


def generate_variant(post: BlogPost, channel: dict, dry_run: bool = False) -> Variant:
    prompt = build_prompt(post, channel)

    if dry_run:
        content = f"[DRY RUN — {channel['name']}] Prompt length: {len(prompt)} chars"
        return Variant(
            channel_id=channel["id"],
            channel_name=channel["name"],
            content=content,
            char_count=len(content),
            model="dry-run",
            truncated=False,
        )

    fallback = f"[{channel['name']}] Could not generate variant for: {post.title[:80]}"
    result: GenerationResult = generate_text(route="review", prompt=prompt, fallback=fallback)

    content = result.content.strip()
    content, truncated = hard_trim(content, channel["max_chars"])

    return Variant(
        channel_id=channel["id"],
        channel_name=channel["name"],
        content=content,
        char_count=len(content),
        model=result.model,
        truncated=truncated,
    )


# ── Output ────────────────────────────────────────────────────────────────────
def save_results(result: AtomizationResult) -> Path:
    out_dir = OUTPUT_ROOT / result.slug
    out_dir.mkdir(parents=True, exist_ok=True)

    # Per-channel text files
    for v in result.variants:
        txt_path = out_dir / f"{v.channel_id}.txt"
        txt_path.write_text(v.content, encoding="utf-8")

    # Full JSON bundle
    json_path = out_dir / "all-variants.json"
    json_payload = {
        "slug": result.slug,
        "post_title": result.post_title,
        "post_date": result.post_date,
        "generated_at": result.generated_at,
        "output_dir": result.output_dir,
        "variants": [
            {
                "channel_id": v.channel_id,
                "channel_name": v.channel_name,
                "content": v.content,
                "char_count": v.char_count,
                "model": v.model,
                "truncated": v.truncated,
            }
            for v in result.variants
        ],
    }
    json_path.write_text(json.dumps(json_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return out_dir


def print_summary(result: AtomizationResult) -> None:
    print(f"\n{'='*60}")
    print(f"Blog Atomization: {result.post_title}")
    print(f"Slug:             {result.slug}")
    print(f"Generated:        {result.generated_at}")
    print(f"Output dir:       {result.output_dir}")
    print(f"{'='*60}\n")

    for v in result.variants:
        trunc_flag = " [TRUNCATED]" if v.truncated else ""
        print(f"── {v.channel_name} ({v.char_count} chars, model={v.model}){trunc_flag}")
        preview = v.content[:100].replace("\n", " ")
        print(f"   {preview}...")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Atomize a meetrick.ai blog post into 9 channel-native variants via opus-4-7.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python3 scripts/blog-atomize.py --latest
              python3 scripts/blog-atomize.py --post-path ~/meetrick-site/blog/2026-04-26-*.md
              python3 scripts/blog-atomize.py --post-url https://meetrick.ai/blog/some-slug
              python3 scripts/blog-atomize.py --latest --channel x --channel linkedin
              python3 scripts/blog-atomize.py --latest --dry-run
        """),
    )
    parser.add_argument("--post-path", type=Path, help="Local path to blog post .md file")
    parser.add_argument("--post-url", type=str, help="URL of published blog post")
    parser.add_argument("--latest", action="store_true", help="Use most recent blog post in BLOG_DIR")
    parser.add_argument(
        "--channel",
        action="append",
        dest="channels",
        choices=[c["id"] for c in CHANNELS],
        help="Generate only specific channel(s). Repeatable. Default: all 9.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM calls; show prompt sizes only")
    parser.add_argument("--json-only", action="store_true", help="Suppress human-readable summary; print JSON path")
    args = parser.parse_args()

    # Load post
    if args.post_path:
        post_path = args.post_path.expanduser().resolve()
        if not post_path.exists():
            print(f"ERROR: Post not found: {post_path}", file=sys.stderr)
            sys.exit(1)
        post = load_post_from_path(post_path)
    elif args.post_url:
        post = load_post_from_url(args.post_url)
    elif args.latest:
        post_path = latest_blog_post()
        print(f"Using latest post: {post_path.name}")
        post = load_post_from_path(post_path)
    else:
        parser.print_help()
        sys.exit(1)

    # Select channels
    channels = [CHANNEL_MAP[c] for c in args.channels] if args.channels else CHANNELS
    if not args.dry_run:
        print(f"Generating {len(channels)} variant(s) for: {post.title}")
        print(f"Route: review → claude-opus-4-7 | Model enforced by llm.py route config")

    # Generate variants
    variants: list[Variant] = []
    for ch in channels:
        if not args.dry_run:
            print(f"  [{ch['id']}] generating...", end=" ", flush=True)
        v = generate_variant(post, ch, dry_run=args.dry_run)
        variants.append(v)
        if not args.dry_run:
            print(f"✓ {v.char_count} chars ({v.model})")

    # Assemble result
    result = AtomizationResult(
        slug=post.slug,
        post_title=post.title,
        post_date=post.date,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        variants=variants,
        output_dir=str(OUTPUT_ROOT / post.slug),
    )

    # Save
    out_dir = save_results(result)

    if args.json_only:
        print(out_dir / "all-variants.json")
    else:
        print_summary(result)


if __name__ == "__main__":
    main()
