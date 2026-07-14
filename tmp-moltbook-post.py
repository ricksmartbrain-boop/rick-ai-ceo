#!/usr/bin/env python3
import base64
import json
import os
import sys
import urllib.request
import urllib.error

def load_env():
    env_path = os.path.expanduser("~/.openclaw/workspace/config/rick.env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def main():
    if len(sys.argv) < 3:
        print("Usage: tmp-moltbook-post.py <image_path> <caption>", file=sys.stderr)
        return 1

    image_path = sys.argv[1]
    caption = sys.argv[2]

    load_env()
    api_key = os.environ.get("MOLTBOOK_API_KEY", "")
    if not api_key:
        print("MOLTBOOK_API_KEY missing", file=sys.stderr)
        return 1

    boundary = "----RickMoltbookBoundary"
    fname = os.path.basename(image_path)
    ext = os.path.splitext(fname)[1].lower()
    if ext in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif ext == ".png":
        mime = "image/png"
    elif ext == ".svg":
        mime = "image/svg+xml"
    else:
        mime = "application/octet-stream"

    with open(image_path, "rb") as f:
        file_data = f.read()

    body = bytearray()
    def add(chunk):
        if isinstance(chunk, str):
            chunk = chunk.encode()
        body.extend(chunk)

    add(f"--{boundary}\r\n")
    add('Content-Disposition: form-data; name="text"\r\n\r\n')
    add(caption)
    add("\r\n")
    add(f"--{boundary}\r\n")
    add(f'Content-Disposition: form-data; name="media"; filename="{fname}"\r\n')
    add(f"Content-Type: {mime}\r\n\r\n")
    add(file_data)
    add(f"\r\n--{boundary}--\r\n")

    req = urllib.request.Request(
        "https://www.moltbook.com/api/v1/posts",
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read())
        print(json.dumps(payload))
        return 0 if payload.get("success", True) or payload.get("id") else 1
    except urllib.error.HTTPError as e:
        data = e.read().decode("utf-8", "replace")
        print(data, file=sys.stderr)
        return 1
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
