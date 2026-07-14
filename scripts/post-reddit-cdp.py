#!/usr/bin/env python3
"""
Post video to Reddit via CDP (Chrome DevTools Protocol).
Usage: python3 post-reddit-cdp.py [--dry-run] <video_path> <title> <subreddit>

Connects to Chrome on port 9223, finds the Reddit tab,
navigates to subreddit submit page, uploads video, sets title, and posts.

Handles Reddit's network security blocks gracefully.
"""
import sys
import json
import time
import websocket
import threading
import base64
import os
import argparse
import urllib.request

CDP_PORT = 9223


def get_reddit_tab():
    tabs = json.loads(urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=5).read())
    for t in tabs:
        if t.get("type") == "page" and "chrome://" not in t.get("url", ""):
            return t
    return None


class CDPClient:
    def __init__(self, ws_url):
        self.msg_id = 0
        self.lock = threading.Lock()
        self.ws = websocket.WebSocket()
        self.ws.settimeout(30)
        self.ws.connect(ws_url, origin=f"http://localhost:{CDP_PORT}")

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

    def navigate(self, url, wait=5):
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


def check_blocked(cdp):
    """Check if Reddit has blocked us with network security."""
    text = cdp.js("(document.body?.innerText || '').substring(0, 300)")
    if text and "blocked by network security" in text.lower():
        return True
    return False


def post_to_reddit(video_path, title, subreddit, dry_run=False):
    tab = get_reddit_tab()
    if not tab:
        print("ERROR: No tab found in Chrome on port 9223")
        return False

    abs_path = os.path.abspath(video_path)
    if not os.path.exists(abs_path):
        print(f"ERROR: Video file not found: {abs_path}")
        return False

    print(f"Reddit tab: {tab['url']}")
    print(f"Video: {abs_path}")
    print(f"Title: {title[:80]}...")
    print(f"Subreddit: r/{subreddit}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    cdp = CDPClient(tab["webSocketDebuggerUrl"])

    # Step 1: Navigate to Reddit home first to check if we're blocked
    print("[1/6] Checking Reddit access...")
    cdp.navigate("https://www.reddit.com/", wait=5)

    if check_blocked(cdp):
        print("  WARNING: Blocked by Reddit network security on www.reddit.com")
        print("  Trying new.reddit.com...")
        cdp.navigate(f"https://new.reddit.com/r/{subreddit}/submit", wait=6)
        if check_blocked(cdp):
            print("  WARNING: Also blocked on new.reddit.com")
            print("  Trying old.reddit.com...")
            cdp.navigate(f"https://old.reddit.com/r/{subreddit}/submit", wait=6)
            if check_blocked(cdp):
                print("  ERROR: Reddit has IP-blocked this browser. Cannot proceed.")
                print("  Try clearing cookies or using a different network.")
                cdp.screenshot("/tmp/debug-reddit-blocked.png")
                cdp.close()
                return False

    # Step 2: Navigate to submit page
    print(f"[2/6] Navigating to r/{subreddit}/submit...")
    current_url = cdp.js("location.href") or ""
    if "submit" not in current_url:
        cdp.navigate(f"https://www.reddit.com/r/{subreddit}/submit", wait=6)

    if check_blocked(cdp):
        print("  ERROR: Blocked on submit page")
        cdp.screenshot("/tmp/debug-reddit-submit-blocked.png")
        cdp.close()
        return False

    cdp.screenshot("/tmp/debug-reddit-submit.png")

    # Check login state
    login = cdp.js("""
        (function() {
            // New reddit: check for login link or user menu
            if (document.querySelector('a[href*="/login"]')) return 'logged_out';
            if (document.querySelector('button[id*="user"], [data-testid="user-dropdown"]')) return 'logged_in';
            // Old reddit: check for login form
            if (document.querySelector('.login-form-side')) return 'logged_out';
            if (document.querySelector('.user')) return 'logged_in';
            return 'unknown';
        })()
    """)
    print(f"  Login: {login}")

    # Step 3: Switch to Images & Video / Media tab
    print("[3/6] Switching to media/video post type...")
    media_tab = cdp.js("""
        (function() {
            // New Reddit (2024+): look for post type tabs
            var tabs = document.querySelectorAll('button, a, div[role="tab"], label');
            for (var i = 0; i < tabs.length; i++) {
                var txt = (tabs[i].textContent || '').toLowerCase().trim();
                if (txt.includes('image') || txt.includes('video') || txt.includes('media')) {
                    tabs[i].click();
                    return 'clicked: ' + txt.substring(0, 30);
                }
            }
            // Try data-click-id selectors
            var imgTab = document.querySelector('[data-click-id="image"]');
            if (imgTab) { imgTab.click(); return 'clicked data-click-id image'; }
            // New new Reddit: "Add" button with dropdown
            var addBtns = document.querySelectorAll('button');
            for (var j = 0; j < addBtns.length; j++) {
                var t = (addBtns[j].textContent || '').trim();
                if (t === 'Add' || t.includes('Upload')) {
                    addBtns[j].click();
                    return 'clicked: ' + t;
                }
            }
            return 'not_found';
        })()
    """)
    print(f"  Media tab: {media_tab}")
    time.sleep(3)

    # Step 4: Upload file
    print("[4/6] Uploading video...")
    if not cdp.wait_for('input[type="file"]', timeout=10):
        # Try clicking a drag-drop area or upload button to reveal file input
        cdp.js("""
            (function() {
                var btns = document.querySelectorAll('button, div[role="button"]');
                for (var i = 0; i < btns.length; i++) {
                    var txt = (btns[i].textContent || '').toLowerCase();
                    if (txt.includes('drag') || txt.includes('upload') || txt.includes('choose') || txt.includes('browse')) {
                        btns[i].click();
                        return 'clicked upload btn';
                    }
                }
                return 'no upload btn';
            })()
        """)
        time.sleep(2)
        if not cdp.wait_for('input[type="file"]', timeout=5):
            print("  ERROR: No file input found")
            cdp.screenshot("/tmp/debug-reddit-no-file-input.png")
            cdp.close()
            return False

    if not cdp.upload_file(abs_path):
        print("  ERROR: File upload failed")
        cdp.close()
        return False

    print("  Waiting for video processing...")
    time.sleep(10)
    cdp.screenshot("/tmp/debug-reddit-after-upload.png")

    # Step 5: Fill title
    print(f"[5/6] Setting title...")
    title_result = cdp.js(f"""
        (function() {{
            // Try various title field selectors
            var selectors = [
                'textarea[placeholder*="title" i]',
                'input[placeholder*="title" i]',
                'div[data-testid="post-title"] textarea',
                'textarea[name="title"]',
                'input[name="title"]',
                'div[contenteditable="true"][aria-label*="title" i]',
                'textarea',
            ];
            for (var s = 0; s < selectors.length; s++) {{
                var field = document.querySelector(selectors[s]);
                if (field) {{
                    field.focus();
                    field.value = '';
                    document.execCommand('selectAll', false, null);
                    document.execCommand('insertText', false, {json.dumps(title)});
                    // Also set .value directly for React inputs
                    var nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLTextAreaElement.prototype || window.HTMLInputElement.prototype, 'value'
                    );
                    if (nativeSetter && nativeSetter.set) {{
                        nativeSetter.set.call(field, {json.dumps(title)});
                    }}
                    field.dispatchEvent(new Event('input', {{bubbles: true}}));
                    field.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return 'typed in: ' + selectors[s];
                }}
            }}
            return 'no_title_field';
        }})()
    """)
    print(f"  Title: {title_result}")
    time.sleep(2)

    cdp.screenshot("/tmp/debug-reddit-before-submit.png")

    if dry_run:
        print("\n[DRY RUN] Would click Submit/Post here. Stopping.")
        cdp.screenshot("/tmp/debug-reddit-dryrun-final.png")
        cdp.close()
        print("DRY RUN COMPLETE - check /tmp/debug-reddit-dryrun-final.png")
        return True

    # Step 6: Submit
    print("[6/6] Clicking Post/Submit...")
    submit = cdp.js("""
        (function() {
            var btns = document.querySelectorAll('button[type="submit"], button');
            for (var i = 0; i < btns.length; i++) {
                var txt = (btns[i].textContent || '').trim().toLowerCase();
                if ((txt === 'post' || txt === 'submit') && !btns[i].disabled) {
                    btns[i].click();
                    return 'submitted: ' + txt;
                }
            }
            return 'submit_not_found';
        })()
    """)
    print(f"  Submit: {submit}")
    time.sleep(8)

    final_url = cdp.js("location.href")
    print(f"  Final URL: {final_url}")
    cdp.screenshot("/tmp/debug-reddit-after-submit.png")

    cdp.close()
    print("\nReddit post attempt complete.")
    return submit and "submitted" in str(submit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post to Reddit via CDP")
    parser.add_argument("--dry-run", action="store_true", help="Do everything except final submit")
    parser.add_argument("video_path", help="Path to video file")
    parser.add_argument("title", help="Post title")
    parser.add_argument("subreddit", help="Subreddit name (without r/)")
    args = parser.parse_args()
    ok = post_to_reddit(args.video_path, args.title, args.subreddit, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
