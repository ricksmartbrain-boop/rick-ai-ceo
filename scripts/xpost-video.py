#!/usr/bin/env python3
"""
xpost-video.py — Native MP4 upload to X (Twitter) with chunked media upload API.

Usage:
    python3 xpost-video.py --video /path/to/file.mp4 --text "caption here"

Auth: Reads X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
      from ~/.openclaw/workspace/config/rick.env automatically.
"""

import argparse
import os
import sys
import time
import subprocess
import math

import requests
from requests_oauthlib import OAuth1


UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
TWEET_URL = "https://api.twitter.com/2/tweets"
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB chunks


def load_env():
    """Load X credentials from keys.env (or rick.env fallback) into os.environ."""
    needed = {"X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"}

    # Primary: ~/.config/x-api/keys.env (same source as xpost CLI)
    keys_file = os.path.expanduser("~/.config/x-api/keys.env")
    if os.path.isfile(keys_file):
        with open(keys_file) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, val = line.partition("=")
                    key = key.strip()
                    if key in needed:
                        os.environ[key] = val.strip()

    # Fallback: rick.env via bash source
    if any(not os.environ.get(k) for k in needed):
        env_path = os.path.expanduser("~/.openclaw/workspace/config/rick.env")
        result = subprocess.run(
            ["bash", "-c", f"source {env_path} && env"],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.split("\n"):
            if "=" in line:
                key, _, val = line.partition("=")
                if key in needed:
                    os.environ.setdefault(key, val)

    missing = [k for k in needed if not os.environ.get(k)]
    if missing:
        print(f"[ERROR] Missing env vars: {missing}", file=sys.stderr)
        print(f"       Looked in: {keys_file} and rick.env", file=sys.stderr)
        sys.exit(1)


def make_auth():
    return OAuth1(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def upload_video(video_path: str) -> str:
    """
    Chunked media upload (INIT → APPEND × N → FINALIZE → poll STATUS).
    Returns media_id_string.
    """
    auth = make_auth()
    file_size = os.path.getsize(video_path)
    total_chunks = math.ceil(file_size / CHUNK_SIZE)

    print(f"[upload] File: {video_path} ({file_size / 1024 / 1024:.1f} MB, {total_chunks} chunks)")

    # --- INIT ---
    resp = requests.post(
        UPLOAD_URL,
        data={
            "command": "INIT",
            "total_bytes": file_size,
            "media_type": "video/mp4",
            "media_category": "tweet_video",
        },
        auth=auth,
    )
    resp.raise_for_status()
    media_id = resp.json()["media_id_string"]
    print(f"[upload] INIT — media_id: {media_id}")

    # --- APPEND ---
    with open(video_path, "rb") as f:
        segment_index = 0
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            resp = requests.post(
                UPLOAD_URL,
                data={
                    "command": "APPEND",
                    "media_id": media_id,
                    "segment_index": segment_index,
                },
                files={"media": chunk},
                auth=auth,
            )
            if resp.status_code not in (200, 204):
                print(f"[ERROR] APPEND chunk {segment_index} failed: {resp.status_code} {resp.text}", file=sys.stderr)
                sys.exit(1)
            print(f"[upload] APPEND chunk {segment_index + 1}/{total_chunks} — OK")
            segment_index += 1

    # --- FINALIZE ---
    resp = requests.post(
        UPLOAD_URL,
        data={"command": "FINALIZE", "media_id": media_id},
        auth=auth,
    )
    resp.raise_for_status()
    finalize_data = resp.json()
    print(f"[upload] FINALIZE — {finalize_data}")

    # --- Poll STATUS if needed ---
    processing = finalize_data.get("processing_info")
    while processing and processing.get("state") not in ("succeeded", "failed"):
        wait = processing.get("check_after_secs", 2)
        print(f"[upload] Processing... state={processing['state']}, waiting {wait}s")
        time.sleep(wait)

        resp = requests.get(
            UPLOAD_URL,
            params={"command": "STATUS", "media_id": media_id},
            auth=auth,
        )
        resp.raise_for_status()
        status_data = resp.json()
        processing = status_data.get("processing_info")
        print(f"[upload] STATUS — {processing}")

    if processing and processing.get("state") == "failed":
        error = processing.get("error", {})
        print(f"[ERROR] Video processing failed: {error}", file=sys.stderr)
        sys.exit(1)

    print(f"[upload] ✅ Video processing complete — media_id: {media_id}")
    return media_id


def post_tweet(text: str, media_id: str) -> dict:
    """Post tweet with native video via X API v2."""
    auth = make_auth()
    payload = {
        "text": text,
        "media": {"media_ids": [media_id]},
    }
    resp = requests.post(TWEET_URL, json=payload, auth=auth)
    if not resp.ok:
        print(f"[ERROR] Tweet post failed: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Post native MP4 video to X (Twitter)")
    parser.add_argument("--video", required=True, help="Path to MP4 file")
    parser.add_argument("--text", required=True, help="Tweet caption")
    args = parser.parse_args()

    if not os.path.isfile(args.video):
        print(f"[ERROR] Video file not found: {args.video}", file=sys.stderr)
        sys.exit(1)

    load_env()

    media_id = upload_video(args.video)
    result = post_tweet(args.text, media_id)

    tweet_id = result.get("data", {}).get("id", "unknown")
    tweet_url = f"https://x.com/i/web/status/{tweet_id}"
    print(f"\n✅ Tweet posted successfully!")
    print(f"   Tweet ID  : {tweet_id}")
    print(f"   Tweet URL : {tweet_url}")
    print(f"   Media ID  : {media_id}")


if __name__ == "__main__":
    main()
