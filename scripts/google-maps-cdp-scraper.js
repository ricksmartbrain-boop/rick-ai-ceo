#!/usr/bin/env node
/**
 * Google Maps scraper via persistent CDP browser.
 *
 * Attaches to a chrome-cdp session, runs a Maps search, and pulls result cards
 * from the side panel (no click-through in v1 — that's a v2 add for phone/email).
 *
 * Usage:
 *   node google-maps-cdp-scraper.js --port 9222 --query "med spa austin tx" --max 30 [--out path.jsonl]
 *
 * Output: one JSON object per line on stdout (jsonlines), then a final
 * summary JSON object on the LAST line. Exit 0 on success, 1 on auth/captcha,
 * 2 on bad args.
 *
 * Anti-detection posture:
 *   - 1.2-2.5s human-typed jitter on the search box (matches LinkedIn-DM script).
 *   - Use page.keyboard.press('Enter') instead of clicking a submit button.
 *   - Slow scroll (300px / 600ms × N) to load lazy results.
 */
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');

function parseArgs() {
  const out = { port: 9222, query: null, max: 30, out: null, dryRun: false, scrolls: 4 };
  const a = process.argv.slice(2);
  for (let i = 0; i < a.length; i++) {
    const v = a[i];
    if (v === '--port') out.port = parseInt(a[++i], 10);
    else if (v === '--query') out.query = a[++i];
    else if (v === '--max') out.max = parseInt(a[++i], 10);
    else if (v === '--out') out.out = a[++i];
    else if (v === '--dry-run') out.dryRun = true;
    else if (v === '--scrolls') out.scrolls = parseInt(a[++i], 10);
  }
  return out;
}

function jsonOut(payload, code = 0) {
  console.log(JSON.stringify(payload));
  process.exit(code);
}

async function humanType(page, text) {
  for (const ch of text) {
    await page.keyboard.type(ch, { delay: 40 + Math.floor(Math.random() * 160) });
  }
}

(async () => {
  const args = parseArgs();
  if (!args.query) {
    jsonOut({ status: 'error', reason: 'missing --query' }, 2);
  }
  if (args.dryRun) {
    jsonOut({ status: 'dry-run', port: args.port, query: args.query, would_scrape: args.max });
  }

  let browser;
  let page;
  try {
    browser = await chromium.connectOverCDP('http://localhost:' + args.port);
  } catch (e) {
    jsonOut({ status: 'error', reason: 'cdp-connect-failed', detail: String(e).slice(0, 240) }, 1);
  }

  try {
    const ctx = browser.contexts()[0] || await browser.newContext();
    page = ctx.pages()[0] || await ctx.newPage();

    // Land on the canonical maps homepage first to keep cookies/session warm.
    await page.goto('https://www.google.com/maps', { waitUntil: 'domcontentloaded', timeout: 25000 });
    await page.waitForTimeout(800 + Math.floor(Math.random() * 1200));

    // CAPTCHA / consent detection — bail with auth-failure exit so the
    // formatter catches it via the same code path as LinkedIn auth fails.
    const url = page.url();
    if (url.includes('/sorry/') || url.includes('captcha') || url.includes('consent.google.com')) {
      jsonOut({ status: 'error', reason: 'captcha-or-consent', url }, 1);
    }

    // Find the search input. Google Maps has rotated selectors; try a few.
    const searchSel = '#searchboxinput, input[name="q"], input[aria-label="Search Google Maps"]';
    const searchBox = page.locator(searchSel).first();
    await searchBox.waitFor({ state: 'visible', timeout: 15000 });
    await searchBox.click({ delay: 120 });
    await page.keyboard.press('Meta+A').catch(() => {});
    await page.keyboard.press('Backspace').catch(() => {});
    await humanType(page, args.query);
    await page.waitForTimeout(400 + Math.floor(Math.random() * 600));
    await page.keyboard.press('Enter');

    // Wait for results panel to render
    await page.waitForSelector('[role="feed"], [aria-label*="Results"], [aria-label*="results"]', { timeout: 20000 });
    await page.waitForTimeout(1500);

    // Scroll the results panel a few times to load more cards
    const feedSel = '[role="feed"], [aria-label*="Results"], [aria-label*="results"]';
    for (let s = 0; s < args.scrolls; s++) {
      await page.evaluate((sel) => {
        const feed = document.querySelector(sel);
        if (feed) feed.scrollBy(0, 1200);
      }, feedSel);
      await page.waitForTimeout(700 + Math.floor(Math.random() * 600));
    }

    // Extract result cards
    const items = await page.evaluate((max) => {
      function txt(el, sel) {
        const n = el.querySelector(sel);
        return n ? (n.textContent || '').trim() : '';
      }
      function attr(el, sel, name) {
        const n = el.querySelector(sel);
        return n ? (n.getAttribute(name) || '') : '';
      }
      const out = [];
      const cards = document.querySelectorAll('[role="feed"] > div > div[jsaction], a[href*="/maps/place/"]');
      const seen = new Set();
      for (const card of cards) {
        if (out.length >= max) break;
        const link = card.matches('a') ? card : card.querySelector('a[href*="/maps/place/"]');
        if (!link) continue;
        const href = link.getAttribute('href') || '';
        if (!href || seen.has(href)) continue;
        seen.add(href);
        // Name lives in aria-label of the <a> for most cards
        const ariaName = link.getAttribute('aria-label') || '';
        const name = ariaName || txt(card, '.qBF1Pd, [class*="fontHeadlineSmall"]');
        const meta = txt(card, '.W4Efsd');  // address + category line
        const rating = txt(card, '[role="img"][aria-label*="stars"], .MW4etd');
        out.push({
          name: name.trim(),
          maps_url: href.startsWith('http') ? href : 'https://www.google.com' + href,
          meta_line: meta,
          rating_label: attr(card, '[role="img"][aria-label*="stars"]', 'aria-label') || rating,
        });
      }
      return out;
    }, args.max);

    for (const it of items) {
      console.log(JSON.stringify({ kind: 'item', ...it }));
    }
    console.log(JSON.stringify({ kind: 'summary', status: 'ok', query: args.query, count: items.length }));
    process.exit(0);
  } catch (e) {
    jsonOut({ status: 'error', reason: String(e).slice(0, 240) }, 1);
  } finally {
    try { if (browser) await browser.close(); } catch (e) { /* connectOverCDP — close is no-op */ }
  }
})();
