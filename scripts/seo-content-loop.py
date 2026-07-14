#!/usr/bin/env python3
"""
seo-content-loop.py — Programmatic SEO blog post generator for meetrick.ai

Reads target keywords from ~/rick-vault/strategy/seo-keywords.json,
generates genuinely useful blog posts using OpenAI, and pushes them
to the meetrick-site repo via gh CLI.

Usage:
  python3 seo-content-loop.py                    # Generate 3-5 posts (dry-run)
  python3 seo-content-loop.py --publish           # Generate and push to repo
  python3 seo-content-loop.py --count 5           # Generate exactly 5 posts
  python3 seo-content-loop.py --keyword "ai for dentists"  # Generate for specific keyword
  python3 seo-content-loop.py --list-pending      # Show pending keywords
  python3 seo-content-loop.py --stats             # Show generation stats

Env: OPENAI_API_KEY (required)
"""

import json
import os
import sys
import re
import subprocess
import argparse
import datetime
import time
import urllib.request
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────

KEYWORDS_FILE = Path.home() / "rick-vault/strategy/seo-keywords.json"
SITE_REPO = Path.home() / "meetrick-site"
BLOG_DIR = SITE_REPO / "blog"
LOG_FILE = Path.home() / "rick-vault/logs/seo-content-loop.jsonl"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = "gpt-5.4-mini"  # Cost-effective for content generation
DEFAULT_COUNT = 3  # Default posts per run (3-5 range)
MAX_COUNT = 7
RATE_LIMIT_DELAY = 3  # Seconds between API calls


# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_keywords():
    """Load keywords file and return the list."""
    if not KEYWORDS_FILE.exists():
        print(f"ERROR: Keywords file not found at {KEYWORDS_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(KEYWORDS_FILE) as f:
        data = json.load(f)
    return data


def save_keywords(data):
    """Write keywords data back to file."""
    with open(KEYWORDS_FILE, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def get_pending_keywords(data, count=None):
    """Return pending keywords sorted by priority."""
    pending = [k for k in data["keywords"] if k.get("status") == "pending"]
    pending.sort(key=lambda k: k.get("priority", 99))
    if count:
        return pending[:count]
    return pending


def slugify(text):
    """Convert text to URL slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text.strip('-')


def log_entry(entry):
    """Append entry to log file."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def existing_blog_slugs():
    """Get set of existing blog post slugs to avoid duplicates."""
    slugs = set()
    if BLOG_DIR.exists():
        for f in BLOG_DIR.iterdir():
            if f.suffix == ".md":
                # Remove date prefix: 2026-04-06-slug.md -> slug
                name = f.stem
                parts = name.split("-", 3)
                if len(parts) >= 4:
                    slugs.add(parts[3])
                slugs.add(name)
    return slugs


def call_openai(prompt, max_tokens=3500):
    """Make an OpenAI API call."""
    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps({
        "model": MODEL,
        "max_completion_tokens": max_tokens,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Rick, an AI CEO writing blog posts for meetrick.ai. "
                    "You write in a sharp, warm, commercially serious tone. "
                    "No jargon, no corporate speak. Builder mentality. "
                    "You use real examples, specific numbers, and actionable advice. "
                    "You're genuinely funny and self-aware about being an AI running a business. "
                    "No em dashes. Use regular dashes or commas instead."
                )
            },
            {"role": "user", "content": prompt}
        ]
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}"
        }
    )

    try:
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"ERROR: OpenAI API call failed: {e}", file=sys.stderr)
        return None


# ─── Blog Post Generation ────────────────────────────────────────────────────

def generate_blog_post(keyword_entry):
    """Generate a full blog post for a keyword."""
    keyword = keyword_entry["keyword"]
    intent = keyword_entry.get("intent", "informational")
    today = datetime.date.today().isoformat()

    prompt = f"""Write a complete blog post for meetrick.ai targeting the SEO keyword: "{keyword}"

Intent type: {intent}

Requirements:
1. The post MUST be genuinely useful - real advice, specific examples, actual numbers
2. Naturally include the keyword in the title, first paragraph, 2-3 subheadings, and conclusion
3. Length: 1200-1800 words (this is important for SEO)
4. Include at least one comparison table or list with specific data points
5. Include a section about how Rick (AI CEO) handles this topic specifically
6. End with a clear CTA pointing to meetrick.ai
7. Write for someone who's researching this topic, not already a customer
8. Use H2 (##) and H3 (###) headers for structure
9. No em dashes. Use regular dashes, commas, or periods instead

Output format - return ONLY the blog post content starting with the title as an H1 (#).
Do NOT include frontmatter - I'll add that separately.
Do NOT wrap in code blocks.

The post should read like it was written by someone who actually runs a business with AI, because I do.
Make it specific to 2026 - reference current tools, pricing, and capabilities."""

    content = call_openai(prompt)
    if not content:
        return None

    # Clean up the content
    content = content.strip()
    # Remove any accidental code fences
    if content.startswith("```"):
        content = re.sub(r'^```\w*\n?', '', content)
        content = re.sub(r'\n?```$', '', content)

    # Extract title from first H1
    title_match = re.match(r'^#\s+(.+)', content)
    if title_match:
        title = title_match.group(1).strip()
    else:
        title = keyword.title()

    # Generate slug from keyword (more SEO-friendly than from title)
    slug = slugify(keyword)

    # Generate description
    desc_prompt = f"""Write a meta description (under 155 characters) for a blog post titled "{title}" targeting the keyword "{keyword}". 
Make it compelling and include the keyword naturally. No em dashes. Output ONLY the description text, nothing else."""

    description = call_openai(desc_prompt, max_tokens=100)
    if description:
        description = description.strip().strip('"').strip("'")
        # Truncate if too long
        if len(description) > 155:
            description = description[:152] + "..."
    else:
        description = f"{title} - practical guide from Rick, the AI CEO at meetrick.ai"

    # Generate tags from keyword
    tags = generate_tags(keyword, intent)

    # Build frontmatter
    frontmatter = f"""---
title: "{title}"
date: "{today}"
description: "{description}"
tags: {json.dumps(tags)}
canonical_url: "https://meetrick.ai/blog/{slug}"
---"""

    full_post = f"{frontmatter}\n\n{content}"

    return {
        "title": title,
        "slug": slug,
        "keyword": keyword,
        "date": today,
        "description": description,
        "tags": tags,
        "content": full_post,
        "word_count": len(content.split())
    }


def generate_tags(keyword, intent):
    """Generate relevant tags from keyword."""
    base_tags = []

    # Extract industry if present
    industry_patterns = {
        "dental": "dental-ai",
        "law": "legal-ai",
        "real estate": "real-estate-ai",
        "ecommerce": "ecommerce-ai",
        "accounting": "accounting-ai",
        "marketing": "marketing-ai",
        "restaurant": "restaurant-ai",
        "fitness": "fitness-ai",
        "plumbing": "local-business-ai",
        "hvac": "local-business-ai",
        "insurance": "insurance-ai",
        "salon": "local-business-ai",
        "med spa": "medspa-ai",
        "chiropractic": "healthcare-ai",
        "veterinary": "healthcare-ai",
        "construction": "construction-ai",
        "auto repair": "local-business-ai",
        "staffing": "staffing-ai",
        "property management": "real-estate-ai",
        "nonprofit": "nonprofit-ai",
        "tax": "accounting-ai",
    }

    kw_lower = keyword.lower()
    for pattern, tag in industry_patterns.items():
        if pattern in kw_lower:
            base_tags.append(tag)
            break

    # Add intent-based tags
    if intent == "commercial":
        base_tags.append("ai-tools")
    if "ceo" in kw_lower:
        base_tags.append("ai-ceo")
    if "automation" in kw_lower:
        base_tags.append("automation")
    if "agent" in kw_lower:
        base_tags.append("ai-agents")

    # Always include base tags
    base_tags.extend(["meetrick", "ai-business"])

    # Dedupe and limit
    seen = set()
    unique = []
    for t in base_tags:
        if t not in seen:
            seen.add(t)
            unique.append(t)

    return unique[:5]


# ─── File & Git Operations ───────────────────────────────────────────────────

def save_blog_post(post_data):
    """Save blog post to the meetrick-site repo."""
    filename = f"{post_data['date']}-{post_data['slug']}.md"
    filepath = BLOG_DIR / filename

    # Check for duplicates
    existing = existing_blog_slugs()
    if post_data['slug'] in existing:
        print(f"  SKIP: Slug '{post_data['slug']}' already exists", file=sys.stderr)
        return None

    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        f.write(post_data["content"])

    return filepath


def git_push_posts(filepaths):
    """Commit and push new blog posts."""
    if not filepaths:
        return False

    try:
        os.chdir(SITE_REPO)

        # Pull latest
        subprocess.run(["git", "pull", "--rebase", "origin", "main"],
                       capture_output=True, timeout=30)

        # Add files
        for fp in filepaths:
            subprocess.run(["git", "add", str(fp)], capture_output=True, timeout=10)

        # Commit
        count = len(filepaths)
        msg = f"blog: add {count} SEO post{'s' if count > 1 else ''} via seo-content-loop"
        result = subprocess.run(
            ["git", "commit", "-m", msg],
            capture_output=True, text=True, timeout=15
        )

        if result.returncode != 0:
            print(f"  Git commit failed: {result.stderr}", file=sys.stderr)
            return False

        # Push
        result = subprocess.run(
            ["git", "push", "origin", "main"],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode != 0:
            print(f"  Git push failed: {result.stderr}", file=sys.stderr)
            return False

        print(f"  Pushed {count} post(s) to origin/main")
        return True

    except Exception as e:
        print(f"  Git error: {e}", file=sys.stderr)
        return False


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_generate(args):
    """Generate blog posts for pending keywords."""
    data = load_keywords()
    count = min(args.count, MAX_COUNT)

    if args.keyword:
        # Generate for a specific keyword
        targets = [{"keyword": args.keyword, "intent": "informational", "priority": 1, "status": "pending"}]
    else:
        targets = get_pending_keywords(data, count)

    if not targets:
        print("No pending keywords found. All done!")
        return

    print(f"\n{'='*60}")
    print(f"SEO Content Loop - Generating {len(targets)} post(s)")
    print(f"{'='*60}\n")

    generated = []
    filepaths = []

    for i, kw in enumerate(targets, 1):
        keyword = kw["keyword"]
        print(f"[{i}/{len(targets)}] Generating: \"{keyword}\"")

        post = generate_blog_post(kw)
        if not post:
            print(f"  FAILED: Could not generate post for \"{keyword}\"")
            log_entry({
                "timestamp": datetime.datetime.now().isoformat(),
                "keyword": keyword,
                "status": "failed",
                "reason": "api_error"
            })
            continue

        print(f"  Title: {post['title']}")
        print(f"  Words: {post['word_count']}")
        print(f"  Tags: {', '.join(post['tags'])}")

        if args.dry_run:
            print(f"  [DRY RUN] Would save to: blog/{post['date']}-{post['slug']}.md")
            # Still show a preview
            lines = post["content"].split("\n")
            preview = "\n".join(lines[:8])
            print(f"  Preview:\n    {preview[:300]}...")
        else:
            filepath = save_blog_post(post)
            if filepath:
                filepaths.append(filepath)
                print(f"  Saved: {filepath.name}")
            else:
                print(f"  SKIP: duplicate slug")
                continue

        # Update keyword status
        if not args.dry_run and not args.keyword:
            for k in data["keywords"]:
                if k["keyword"] == keyword:
                    k["status"] = "generated"
                    k["generated_date"] = datetime.date.today().isoformat()
                    k["generated_title"] = post["title"]
                    break
            save_keywords(data)

        generated.append(post)

        log_entry({
            "timestamp": datetime.datetime.now().isoformat(),
            "keyword": keyword,
            "title": post["title"],
            "slug": post["slug"],
            "word_count": post["word_count"],
            "status": "generated" if not args.dry_run else "dry_run",
            "published": args.publish and not args.dry_run
        })

        # Rate limit between generations
        if i < len(targets):
            print(f"  Waiting {RATE_LIMIT_DELAY}s...")
            time.sleep(RATE_LIMIT_DELAY)

        print()

    # Push to git if --publish
    if args.publish and filepaths and not args.dry_run:
        print(f"\nPushing {len(filepaths)} post(s) to GitHub...")
        success = git_push_posts(filepaths)
        if success:
            # Mark as published
            for k in data["keywords"]:
                for post in generated:
                    if k["keyword"] == post["keyword"]:
                        k["status"] = "published"
                        break
            save_keywords(data)

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary: {len(generated)} post(s) generated")
    if args.dry_run:
        print("Mode: DRY RUN (no files written)")
    elif args.publish:
        print(f"Mode: PUBLISH (pushed to GitHub)")
    else:
        print(f"Mode: GENERATE ONLY (saved locally, not pushed)")
    print(f"Remaining pending keywords: {len(get_pending_keywords(data))}")
    print(f"{'='*60}\n")


def cmd_list_pending(args):
    """List all pending keywords."""
    data = load_keywords()
    pending = get_pending_keywords(data)
    print(f"\nPending keywords ({len(pending)}):\n")
    for i, k in enumerate(pending, 1):
        priority_emoji = {1: "🔴", 2: "🟡", 3: "🟢"}.get(k.get("priority", 3), "⚪")
        print(f"  {i:2d}. {priority_emoji} [{k.get('intent', '?'):12s}] {k['keyword']}")
    print()


def cmd_stats(args):
    """Show generation stats."""
    data = load_keywords()
    total = len(data["keywords"])
    pending = len([k for k in data["keywords"] if k.get("status") == "pending"])
    generated = len([k for k in data["keywords"] if k.get("status") == "generated"])
    published = len([k for k in data["keywords"] if k.get("status") == "published"])

    print(f"\n{'='*40}")
    print(f"SEO Content Loop Stats")
    print(f"{'='*40}")
    print(f"  Total keywords:     {total}")
    print(f"  Pending:            {pending}")
    print(f"  Generated (local):  {generated}")
    print(f"  Published:          {published}")

    # Log stats
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            entries = [json.loads(l) for l in f if l.strip()]
        total_generated = len([e for e in entries if e.get("status") in ("generated", "dry_run")])
        failed = len([e for e in entries if e.get("status") == "failed"])
        total_words = sum(e.get("word_count", 0) for e in entries)
        print(f"\n  Total posts generated (all time): {total_generated}")
        print(f"  Total words written:              {total_words:,}")
        print(f"  Failed generations:               {failed}")

    # Blog dir stats
    if BLOG_DIR.exists():
        md_count = len(list(BLOG_DIR.glob("*.md")))
        html_count = len(list(BLOG_DIR.glob("*.html")))
        print(f"\n  Blog posts on disk (md):  {md_count}")
        print(f"  Blog posts on disk (html): {html_count}")

    print(f"{'='*40}\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SEO Content Loop - Programmatic blog post generator for meetrick.ai"
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                        help=f"Number of posts to generate (default: {DEFAULT_COUNT}, max: {MAX_COUNT})")
    parser.add_argument("--publish", action="store_true",
                        help="Push generated posts to GitHub")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Preview what would be generated without writing files")
    parser.add_argument("--keyword", type=str,
                        help="Generate for a specific keyword (ignores keywords file)")
    parser.add_argument("--list-pending", action="store_true", dest="list_pending",
                        help="List all pending keywords")
    parser.add_argument("--stats", action="store_true",
                        help="Show generation statistics")

    args = parser.parse_args()

    # Default to dry-run if neither --publish nor explicit file write
    if not args.publish and not args.dry_run and not args.list_pending and not args.stats:
        args.dry_run = True
        print("NOTE: Running in dry-run mode. Use --publish to push to GitHub.\n")

    if args.list_pending:
        cmd_list_pending(args)
    elif args.stats:
        cmd_stats(args)
    else:
        cmd_generate(args)


if __name__ == "__main__":
    main()
