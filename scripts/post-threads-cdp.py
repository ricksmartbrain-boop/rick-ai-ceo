#!/usr/bin/env python3
"""
Post a video to Threads via CDP (Chrome DevTools Protocol).
Usage: python3 post-threads-cdp.py [--dry-run] <video_path> <caption>

Connects to Chrome on port 9222, finds the Threads tab,
opens compose modal, uploads video, types caption, and posts.
"""
import sys
import json
import time
import websocket
import threading
import base64
import os
import argparse

CDP_PORT = 9222


def get_threads_tab():
    import urllib.request
    tabs = json.loads(urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=5).read())
    for t in tabs:
        if "threads" in t.get("url", "") and t.get("type") == "page":
            return t
    return None


class CDPClient:
    def __init__(self, ws_url):
        self.msg_id = 0
        self.lock = threading.Lock()
        self.ws = websocket.WebSocket()
        self.ws.settimeout(30)
        self.ws.connect(ws_url, suppress_origin=True)

    def send(self, method, params=None):
        with self.lock:
            self.msg_id += 1
            mid = self.msg_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        for _ in range(600):
            try:
                data = json.loads(self.ws.recv())
                if data.get("id") == mid:
                    return data
            except websocket.WebSocketTimeoutException:
                pass
            time.sleep(0.05)
        return {}

    def navigate(self, url, wait=4):
        self.send("Page.navigate", {"url": url})
        time.sleep(wait)

    def js(self, expression):
        r = self.send("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
            "timeout": 15000,
        })
        return r.get("result", {}).get("result", {}).get("value")

    def screenshot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        data = r.get("result", {}).get("data", "")
        if data:
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))
            print(f"  Screenshot: {path}")

    def wait_for(self, selector, timeout=15):
        for _ in range(timeout * 2):
            found = self.js(f"document.querySelector('{selector}') ? true : false")
            if found:
                return True
            time.sleep(0.5)
        return False

    def upload_file(self, file_path):
        """Upload a file via DOM.setFileInputFiles using backendNodeId."""
        r = self.send("Runtime.evaluate", {
            "expression": "document.querySelector('input[type=\"file\"]')",
            "returnByValue": False,
        })
        object_id = r.get("result", {}).get("result", {}).get("objectId")
        if not object_id:
            print("  ERROR: No file input objectId")
            return False

        desc = self.send("DOM.describeNode", {"objectId": object_id})
        backend_node_id = desc.get("result", {}).get("node", {}).get("backendNodeId", 0)
        if not backend_node_id:
            print("  ERROR: No backendNodeId")
            return False

        print(f"  File input backendNodeId: {backend_node_id}")
        result = self.send("DOM.setFileInputFiles", {
            "files": [file_path],
            "backendNodeId": backend_node_id,
        })
        err = result.get("error")
        if err:
            print(f"  ERROR uploading: {err}")
            return False
        print(f"  File uploaded successfully")
        return True

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def post_to_threads(video_path, caption, dry_run=False):
    tab = get_threads_tab()
    if not tab:
        print("ERROR: No Threads tab found in Chrome on port 9222")
        sys.exit(1)

    text_only = not video_path
    abs_path = None
    if not text_only:
        abs_path = os.path.abspath(video_path)
        if not os.path.exists(abs_path):
            print(f"ERROR: Video file not found: {abs_path}")
            sys.exit(1)

    print(f"Threads tab: {tab['url']}")
    print(f"Video: {abs_path if abs_path else '(text-only)'}")
    print(f"Caption: {caption[:80]}...")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    cdp = CDPClient(tab["webSocketDebuggerUrl"])

    # Step 1: Navigate to Threads home (login redirect handles auth)
    print("[1/5] Navigating to Threads...")
    cdp.navigate("https://www.threads.com/login", wait=4)

    # Check if logged in by looking for the compose area
    logged_in = cdp.js("""
        document.querySelector('div[aria-label*="Type to compose"]') ? 'logged_in' :
        document.querySelector('a[href*="/@"]') ? 'logged_in' : 'not_logged_in'
    """)
    print(f"  Login state: {logged_in}")
    if logged_in != "logged_in":
        print("ERROR: Not logged into Threads. Navigate to threads.com/login manually first.")
        cdp.screenshot("/tmp/debug-threads-not-logged-in.png")
        cdp.close()
        sys.exit(1)

    # Step 2: Open compose modal by clicking the compose area
    print("[2/5] Opening compose modal...")
    compose = cdp.js("""
        (function() {
            // Click the "What's new?" compose area
            var compose = document.querySelector('div[aria-label*="Type to compose"]');
            if (compose) { compose.click(); return 'clicked_compose'; }
            // Fallback: click Create in sidebar
            var divs = document.querySelectorAll('div');
            for (var i = 0; i < divs.length; i++) {
                if (divs[i].textContent.trim() === 'Create' && divs[i].children.length < 3) {
                    divs[i].click();
                    return 'clicked_create';
                }
            }
            return 'not_found';
        })()
    """)
    print(f"  Compose: {compose}")
    time.sleep(3)

    # Step 3: Upload video file (skipped for text-only posts)
    if text_only:
        print("[3/5] Text-only post: skipping media upload.")
    else:
        print("[3/5] Uploading video...")

        # Check file input exists
        if not cdp.wait_for('input[type="file"]', timeout=10):
            print("  ERROR: No file input found")
            cdp.screenshot("/tmp/debug-threads-no-file-input.png")
            cdp.close()
            sys.exit(1)

        if not cdp.upload_file(abs_path):
            print("  ERROR: File upload failed")
            cdp.close()
            sys.exit(1)

        # Wait for video processing
        print("  Waiting for video processing...")
        time.sleep(8)
        cdp.screenshot("/tmp/debug-threads-after-upload.png")

    # Step 4: Type caption
    print("[4/5] Typing caption...")
    typed = cdp.js(f"""
        (function() {{
            // Find the contenteditable compose field
            var field = document.querySelector('div[aria-label*="Type to compose"]');
            if (!field) {{
                // Try other contenteditable fields
                var fields = document.querySelectorAll('div[contenteditable="true"], div[role="textbox"]');
                field = fields[0];
            }}
            if (field) {{
                field.focus();
                // Clear any existing text
                document.execCommand('selectAll', false, null);
                document.execCommand('delete', false, null);
                // Type the caption
                document.execCommand('insertText', false, {json.dumps(caption)});
                return 'typed';
            }}
            return 'no_field';
        }})()
    """)
    print(f"  Caption: {typed}")
    time.sleep(1)

    cdp.screenshot("/tmp/debug-threads-before-post.png")

    if dry_run:
        print("\n[DRY RUN] Would click Post here. Stopping.")
        cdp.screenshot("/tmp/debug-threads-dryrun-final.png")
        cdp.close()
        print("DRY RUN COMPLETE - check /tmp/debug-threads-dryrun-final.png")
        return True

    # Step 5: Click Post
    print("[5/5] Clicking Post...")
    post = cdp.js("""
        (function() {
            var btns = document.querySelectorAll('div[role="button"], button');
            for (var i = 0; i < btns.length; i++) {
                var text = (btns[i].textContent || '').trim();
                if (text === 'Post') {
                    btns[i].click();
                    return 'posted';
                }
            }
            return 'post_not_found';
        })()
    """)
    print(f"  Post result: {post}")

    time.sleep(8)
    cdp.screenshot("/tmp/debug-threads-after-post.png")

    # Check final state
    final = cdp.js("document.title + ' | ' + location.href")
    print(f"  Final state: {final}")

    cdp.close()
    print("\nThreads post attempt complete.")
    return post == "posted"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post to Threads via CDP")
    parser.add_argument("--dry-run", action="store_true", help="Do everything except final submit")
    parser.add_argument("--text-only", action="store_true", help="Post caption as a text-only thread (no media)")
    parser.add_argument("video_path", nargs="?", default=None, help="Path to video file (omit with --text-only)")
    parser.add_argument("caption", nargs="?", default=None, help="Post caption")
    args = parser.parse_args()

    # Support both `--text-only "caption"` and `<video> "caption"` invocations.
    video_path = args.video_path
    caption = args.caption
    if args.text_only:
        # In text-only mode the single positional is the caption.
        if caption is None and video_path is not None:
            caption = video_path
        video_path = None
    if not caption:
        parser.error("caption is required")
    if not args.text_only and not video_path:
        parser.error("video_path is required unless --text-only is set")
    post_to_threads(video_path, caption, dry_run=args.dry_run)
