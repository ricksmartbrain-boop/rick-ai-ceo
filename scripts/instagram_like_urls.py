#!/usr/bin/env python3
from __future__ import annotations

import time
from playwright.sync_api import sync_playwright

CDP_URL = "http://localhost:9222"
URLS = [
    "https://www.instagram.com/sahilbloom/p/DX1hEjLEUxU/",
    "https://www.instagram.com/sahilbloom/p/DXz2q0dEubV/",
    "https://www.instagram.com/sahilbloom/p/DXuAUnADgaX/",
]


def connect():
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context(viewport={"width": 1366, "height": 900})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return p, browser, ctx, page


def click_like_if_available(page):
    return page.evaluate("""
    () => {
      const els = [...document.querySelectorAll('button, div[role="button"]')];
      const target = els.find(el => {
        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
        const a = (el.getAttribute('aria-label') || '').trim().toLowerCase();
        return (t === 'like' || a.startsWith('like')) && !a.startsWith('unlike');
      });
      if (!target) return 'no-target';
      target.click();
      return 'clicked';
    }
    """)


def main():
    p, browser, ctx, page = connect()
    try:
        results = []
        for url in URLS:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2.5)
            res = click_like_if_available(page)
            results.append((url, res, page.url))
            print({"url": url, "result": res, "page": page.url}, flush=True)
            time.sleep(1)
        print({"results": results}, flush=True)
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
    main()
