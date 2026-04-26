#!/usr/bin/env python3
"""Post to Threads using the authenticated Chrome session on CDP port 9222."""
import argparse
import os
import sys
import time

from playwright.sync_api import sync_playwright

CDP_URL = "http://localhost:9222"


def open_threads_page():
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(CDP_URL)
    contexts = browser.contexts
    ctx = contexts[0] if contexts else browser.new_context(viewport={"width": 1366, "height": 768})
    pages = ctx.pages
    page = next((pg for pg in pages if "threads" in pg.url), pages[0] if pages else ctx.new_page())
    return p, browser, ctx, page


def handle_cookie_consent(page):
    """Dismiss Threads/Meta cookie consent modal if present."""
    try:
        allow_btn = page.locator('text="Allow all cookies"').first
        if allow_btn.count() > 0 and allow_btn.is_visible(timeout=2000):
            allow_btn.click()
            print("  Dismissed cookie consent.")
            time.sleep(2)
    except Exception:
        pass  # No cookie dialog — fine


def is_logged_in(page):
    """Return True only if the session has an authenticated Threads account."""
    # Positive signal: the inline composer is present (only shown to authenticated users)
    try:
        composer_present = page.locator('[aria-label*="Empty text field"][role="button"]').count() > 0
        if composer_present:
            return True
    except Exception:
        pass
    # Logged-out state: "Log in" link visible in nav area
    try:
        login_visible = page.locator('text="Log in"').is_visible(timeout=1500)
        if login_visible:
            return False
    except Exception:
        pass
    # Also check for signup gate
    try:
        signup_visible = page.locator('text="Sign up to post"').is_visible(timeout=1500)
        if signup_visible:
            return False
    except Exception:
        pass
    return True


def attempt_login(page):
    """Try to log in via 'Continue with Instagram' button (uses stored browser cookies)."""
    print("  Not logged in. Navigating to /login to use Instagram SSO...")
    try:
        page.goto("https://www.threads.com/login", wait_until="domcontentloaded", timeout=20000)
        time.sleep(3)
        handle_cookie_consent(page)

        # DIV text is 'Continue with Instagram\nmeet_rick_ai' — use JS dispatch to avoid intercept
        clicked = page.evaluate("""
            () => {
                const all = [...document.querySelectorAll('div,button,a,[role="button"]')];
                const hit = all.find(el => (el.innerText || '').includes('Continue with Instagram'));
                if (hit) {
                    hit.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    return 'clicked: ' + (hit.innerText || '').substring(0, 50).trim().split(' ').slice(0,5).join(' ');
                }
                return 'not_found';
            }
        """)
        print(f"  Login click: {clicked}")
        if clicked == 'not_found':
            return False

        time.sleep(10)
        current_url = page.url
        print(f"  Post-login URL: {current_url}")
        # If still on /login or redirected to instagram.com, auth didn't complete
        if "/login" in current_url or ("instagram.com" in current_url and "threads" not in current_url):
            print("  Login did not complete — still on login/instagram page.")
            return False
        return True
    except Exception as e:
        print(f"  Login attempt failed: {e}")
    return False


def session_ok(page):
    page.goto("https://www.threads.com/", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    url = page.url
    if "/login" in url or ("instagram.com" in url and "threads" not in url):
        return False

    # Dismiss cookie consent before checking auth state
    handle_cookie_consent(page)

    if is_logged_in(page):
        return True

    # Not logged in — try recovering via "Continue with Instagram"
    if attempt_login(page):
        handle_cookie_consent(page)
        return is_logged_in(page)

    return False


def click_compose(page):
    """Click the compose / new-thread trigger. Works around SVG pointer-intercept issue."""
    clicked = page.evaluate("""
        () => {
            // Strategy 0 (highest priority): inline feed composer — only rendered when authenticated.
            // As of 2026-04-26: div[role="button"] with aria-label containing "Empty text field".
            // Note: avoid CSS selector with apostrophes (What's new?) — match on aria-label instead.
            const allBtns = [...document.querySelectorAll('[role="button"]')];
            const inlineComposer = allBtns.find(el => {
                const label = (el.getAttribute('aria-label') || '').toLowerCase();
                return label.includes('empty text field') || label.includes('type to compose');
            });
            if (inlineComposer) {
                inlineComposer.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                return 'inline-composer:' + (inlineComposer.getAttribute('aria-label') || '').substring(0, 40);
            }

            // Strategy 1: sidebar Create SVG (walks up to clickable parent)
            const svgCreate = document.querySelector('[aria-label="Create"]');
            if (svgCreate) {
                const parent = svgCreate.closest('a,button,[role="button"],[role="link"]') || svgCreate.parentElement;
                if (parent) {
                    parent.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    return 'sidebar-create:' + parent.tagName;
                }
            }
            // Strategy 2: text/label match on Create / New thread buttons
            const candidates = [...document.querySelectorAll('a,button,[role="button"]')];
            const hit = candidates.find(el => {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                const label = (el.getAttribute('aria-label') || '').toLowerCase();
                return t === 'create' || t === 'new thread' || label === 'create' || label.includes('new thread');
            });
            if (hit) {
                hit.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                return 'text-match:' + (hit.innerText || '').trim().substring(0, 30);
            }
            // Strategy 3: pencil/compose SVG → nav container
            const allSvgs = [...document.querySelectorAll('svg[aria-label]')];
            const composeSvg = allSvgs.find(s => /create|compose|new/i.test(s.getAttribute('aria-label') || ''));
            if (composeSvg) {
                const nav = composeSvg.closest('nav > *, nav') || composeSvg.parentElement;
                if (nav) {
                    nav.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    return 'svg-nav-fallback:' + nav.tagName;
                }
            }
            return 'not_found';
        }
    """)
    print(f"  Compose click: {clicked}")
    time.sleep(3)


def find_textbox(page):
    """Find the Threads compose textbox. Tries multiple selectors in priority order.

    Selector audit 2026-04-26: Threads uses a contenteditable div with:
      role="textbox", aria-label="Empty text field. Type to compose a new post.",
      aria-placeholder="What's new?"  (confirmed via live DOM probe).
    The old data-placeholder / Add-to-thread selectors no longer appear in the compose dialog.
    """
    SELECTORS = [
        # Tier 1 — exact aria-label/placeholder as of 2026-04-26 (most specific)
        '[aria-label*="Empty text field"][contenteditable="true"]',
        '[aria-placeholder="What\'s new?"][contenteditable="true"]',
        # Tier 2 — role+contenteditable (robust across minor DOM changes)
        'div[contenteditable="true"][role="textbox"]',
        '[contenteditable="true"][role="textbox"]',
        # Tier 3 — dialog-scoped fallbacks
        '[role="dialog"] [contenteditable="true"]',
        '[aria-modal="true"] [contenteditable="true"]',
        # Tier 4 — legacy selectors kept as last-resort
        'p[data-placeholder][contenteditable]',
        '[aria-placeholder][contenteditable="true"]',
        '[contenteditable="true"]',
        'textarea',
    ]

    for attempt in range(12):
        for sel in SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    print(f"  Found textbox via: {sel}")
                    return loc
            except Exception:
                continue
        time.sleep(1)

    return None


def post_to_threads(video_path, caption, dry_run=False):
    p, browser, ctx, page = open_threads_page()
    try:
        print("Pre-flight: checking Threads session...")
        if not session_ok(page):
            print("SESSION_EXPIRED — could not restore session")
            return False
        print(f"  Landed on: {page.url}")
        print("  Session valid.")

        print("Opening compose dialog...")
        click_compose(page)

        # If no dialog opened yet, try once more after a wait
        time.sleep(2)
        modal = page.locator('[role="dialog"], [aria-modal="true"]').count()
        if modal == 0:
            print("  No dialog after first click — retrying compose click...")
            click_compose(page)
            time.sleep(3)

        print("Typing caption...")
        textbox = find_textbox(page)
        if not textbox:
            print("ERROR: No compose textbox found")
            page.screenshot(path="/Users/rickthebot/.openclaw/workspace/debug-threads-no-textbox.png")
            return False
        textbox.click()
        textbox.focus()
        page.keyboard.type(caption, delay=5)
        time.sleep(1)

        if video_path:
            abs_video = os.path.abspath(video_path)
            if not os.path.exists(abs_video):
                print(f"ERROR: Video not found: {abs_video}")
                return False
            print(f"Attaching video: {abs_video}")
            try:
                with page.expect_file_chooser(timeout=5000) as fc_info:
                    page.evaluate("""
                        () => {
                            const inp = document.querySelector('input[type="file"]');
                            if (inp) { inp.click(); return; }
                            const btns = [...document.querySelectorAll('button,[role="button"],svg')];
                            const found = btns.find(el => {
                                const label = ((el.getAttribute('aria-label') || '') + ' ' + (el.innerText || el.textContent || '')).toLowerCase();
                                return label.includes('attach') || label.includes('media') || label.includes('photo') || label.includes('clip');
                            });
                            if (found) (found.closest('button,[role="button"]') || found).click();
                        }
                    """)
                fc_info.value.set_files(abs_video)
                print("  Video attached.")
                time.sleep(4)
            except Exception as e:
                print(f"  Could not attach video (posting text-only): {e}")

        if dry_run:
            print("\n[DRY RUN] Would click Post here. Stopping.")
            page.screenshot(path="/Users/rickthebot/.openclaw/workspace/debug-threads-dryrun.png")
            print("DRY RUN COMPLETE")
            return True

        print("Submitting post...")
        result = page.evaluate("""
            () => {
                const btns = [...document.querySelectorAll('button,[role="button"]')];
                const post = btns.find(b => ((b.textContent || '').trim() === 'Post') && !b.disabled);
                if (post) { post.click(); return 'POSTED'; }
                return 'no_post_btn';
            }
        """)
        print(f"  Result: {result}")
        time.sleep(8)
        print(f"  Final URL: {page.url}")
        return result == 'POSTED'
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
    parser = argparse.ArgumentParser(description="Post to Threads using the existing Chrome session")
    parser.add_argument("--video", help="Path to video file to upload")
    parser.add_argument("--caption", required=True, help="Post caption text")
    parser.add_argument("--dry-run", action="store_true", help="Do everything except submit")
    args = parser.parse_args()
    ok = post_to_threads(args.video, args.caption, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
