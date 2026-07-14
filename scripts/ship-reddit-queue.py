#!/usr/bin/env python3
"""Ship all queued Reddit posts from distribution-queue.json using post-reddit-api.py."""
import argparse
import json
import os
import subprocess
import sys

QUEUE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "distribution-queue.json")
MEME_DIR = os.path.expanduser("~/rick-vault/memes/c-level-ai-fear")
POSTER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "post-reddit-api.py")


def main():
    parser = argparse.ArgumentParser(description="Ship all queued Reddit posts")
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run to each post")
    parser.add_argument("--queue", default=QUEUE_FILE, help="Path to queue JSON file")
    args = parser.parse_args()

    if not os.path.exists(args.queue):
        print(f"ERROR: Queue file not found: {args.queue}")
        sys.exit(1)

    with open(args.queue) as f:
        queue = json.load(f)

    items = queue.get("queued", [])
    if not items:
        print("Queue is empty.")
        return

    print(f"Processing {len(items)} queued items...")
    print()

    total = 0
    success = 0
    failed = 0

    for item in items:
        title = item.get("reddit_title", "")
        caption = item.get("x_caption", "")
        subreddits = item.get("subreddits", [])
        video_file = item.get("file", "")

        if not title or not subreddits:
            print(f"SKIP: Missing title or subreddits for {video_file}")
            continue

        for sub in subreddits:
            total += 1
            print(f"[{total}] r/{sub}: {title[:60]}...")

            cmd = [
                sys.executable, POSTER_SCRIPT,
                "--title", title,
                "--subreddit", sub,
                "--body", caption,
            ]
            if args.dry_run:
                cmd.append("--dry-run")

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                print(result.stdout.strip())
                if result.stderr.strip():
                    print(f"  stderr: {result.stderr.strip()}")
                if result.returncode == 0:
                    success += 1
                else:
                    failed += 1
                    print(f"  FAILED (exit {result.returncode})")
            except subprocess.TimeoutExpired:
                failed += 1
                print("  TIMEOUT")
            except Exception as e:
                failed += 1
                print(f"  ERROR: {e}")

            print()

    print(f"Done: {success}/{total} succeeded, {failed} failed.")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
