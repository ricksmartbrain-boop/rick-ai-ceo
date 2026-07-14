#!/usr/bin/env python3
"""Post an Instagram Reel using the authenticated Chrome session on CDP port 9222."""
import argparse
import os
import sys
import time

from playwright.sync_api import sync_playwright

CDP_URL = "http://localhost:9222"


def open_instagram_page():
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(CDP_URL)
    contexts = browser.contexts
    ctx = contexts[0] if contexts else browser.new_context(viewport={"width": 1366, "height": 768})
    pages = ctx.pages
    page = next((pg for pg in pages if "instagram.com" in pg.url), pages[0] if pages else ctx.new_page())
    return p, browser, ctx, page


def click_create_and_post(page):
    page.evaluate("""
        () => {
            const els = [...document.querySelectorAll('a,button,div[role="button"]')];
            const found = els.find(el => {
                const text = (el.textContent || '').trim().toLowerCase();
                const label = (el.getAttribute('aria-label') || '').toLowerCase();
                return text.includes('create') || text.includes('new post') || label.includes('create') || label.includes('new post');
            });
            if (found) (found.closest('a,button,div[role="button"]') || found).click();
        }
    """)
    time.sleep(1)
    page.evaluate("""
        () => {
            const els = [...document.querySelectorAll('a,button,div[role="button"],div[role="menuitem"],li')];
            const found = els.find(el => {
                const text = ((el.innerText || el.textContent || '').trim()).toLowerCase();
                const label = (el.getAttribute('aria-label') || '').toLowerCase();
                return text.includes('post') || label.includes('post');
            });
            if (found) (found.closest('a,button,div[role="button"],div[role="menuitem"]') || found).click();
        }
    """)
    time.sleep(2)


def post_reel(video_path, caption, dry_run=False):
    abs_path = os.path.abspath(video_path)
    if not os.path.exists(abs_path):
        print(f"ERROR: Video file not found: {abs_path}")
        return False

    p, browser, ctx, page = open_instagram_page()
    try:
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        login = page.evaluate("document.querySelector('a[href*=\"/accounts/login\"]') ? 'logged_out' : 'logged_in'")
        print(f"Instagram tab: {page.url}")
        print(f"Video: {abs_path}")
        print(f"Caption: {caption[:80]}...")
        print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
        print()
        print("[1/6] Navigating to Instagram...")
        print(f"  Login state: {login}")
        if login == 'logged_out':
            print("ERROR: Not logged into Instagram")
            return False

        print("[2/6] Opening create post modal...")
        click_create_and_post(page)

        print("[3/6] Waiting for file input...")
        page.wait_for_selector('input[type="file"]', state='attached', timeout=20000)
        print("  File input found")

        print("[4/6] Uploading video...")
        page.set_input_files('input[type="file"]', abs_path)
        print("  File uploaded successfully")
        print("  Waiting for video processing...")
        time.sleep(8)
        page.screenshot(path="/Users/rickthebot/.openclaw/workspace/debug-ig-after-upload.png", full_page=False)

        print("[5/6] Navigating create flow...")
        # Dismiss 'Video posts are now shared as reels' OK modal if present
        ok_dismissed = page.evaluate("""
            () => {
                const btns = [...document.querySelectorAll('button, div[role="button"]')];
                const ok = btns.find(el => (el.innerText || el.textContent || '').trim() === 'OK');
                if (ok) { ok.click(); return true; }
                return false;
            }
        """)
        if ok_dismissed:
            print("  Dismissed 'Video posts as reels' modal")
            time.sleep(2)
        for step in range(3):
            # Use JS click to bypass pointer-event interception from overlay divs
            clicked = page.evaluate("""
                () => {
                    const els = [...document.querySelectorAll('button, div[role="button"]')];
                    const found = els.find(el => {
                        const t = (el.innerText || el.textContent || '').trim();
                        const l = (el.getAttribute('aria-label') || '').trim();
                        return t === 'Next' || l === 'Next';
                    });
                    if (found) { found.click(); return true; }
                    return false;
                }
            """)
            print(f"  Step {step+1} Next: {'next_clicked' if clicked else 'no_next'}")
            if not clicked:
                break
            time.sleep(3)

        print("  Looking for caption field...")
        caption_box = None
        for sel in ['div[contenteditable="true"]', 'textarea', 'div[role="textbox"]']:
            loc = page.locator(sel)
            if loc.count() > 0:
                caption_box = loc.first
                break
        if caption_box:
            caption_box.click()
            caption_box.fill(caption)
            caption_found = 'typed_caption'
        else:
            caption_found = 'no_caption_field'
        print(f"  Caption: {caption_found}")
        page.screenshot(path="/Users/rickthebot/.openclaw/workspace/debug-ig-before-share.png", full_page=False)

        if dry_run:
            print("\n[DRY RUN] Would click Share here. Stopping.")
            page.screenshot(path="/Users/rickthebot/.openclaw/workspace/debug-ig-dryrun-final.png", full_page=False)
            print("DRY RUN COMPLETE - check /Users/rickthebot/.openclaw/workspace/debug-ig-dryrun-final.png")
            return True

        print("[6/6] Clicking Share...")
        share = False
        for i in range(page.locator('button, div[role="button"]').count()):
            el = page.locator('button, div[role="button"]').nth(i)
            text = (el.inner_text(timeout=1000) or '').strip().lower() if hasattr(el, 'inner_text') else ''
            label = (el.get_attribute('aria-label') or '').lower()
            if text in ('share', 'publish') or label in ('share', 'publish'):
                el.click()
                share = True
                break
        print(f"  Share result: {'shared' if share else 'share_not_found'}")
        time.sleep(10)
        page.screenshot(path="/Users/rickthebot/.openclaw/workspace/debug-ig-after-share.png", full_page=False)
        return share
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post Instagram Reel via CDP")
    parser.add_argument("--dry-run", action="store_true", help="Do everything except final submit")
    parser.add_argument("video_path", help="Path to video file")
    parser.add_argument("caption", help="Post caption")
    args = parser.parse_args()
    ok = post_reel(args.video_path, args.caption, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
