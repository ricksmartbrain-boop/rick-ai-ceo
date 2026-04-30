#!/usr/bin/env node
/**
 * linkedin-post-v4.js — Post to LinkedIn via CDP.
 *
 * Attaches to a persistent chrome-cdp-linkedin session.
 * Accepts --port, --body, --dry-run.
 *
 * Usage:
 *   node linkedin-post-v4.js --port 9225 --body "Post text here" [--dry-run]
 *
 * Returns JSON on stdout. Exit 0 on success, 1 on failure.
 * Auth failures print "Fatal: ... login" so formatter catches as AuthFailure.
 */
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
const fs = require('fs');

function parseArgs() {
  const out = { port: 9225, body: null, dryRun: false };
  const a = process.argv.slice(2);
  for (let i = 0; i < a.length; i++) {
    const v = a[i];
    if (v === '--port') out.port = parseInt(a[++i], 10);
    else if (v === '--body') out.body = a[++i];
    else if (v === '--dry-run') out.dryRun = true;
  }
  return out;
}

function jsonOut(payload, code = 0) {
  process.stdout.write(JSON.stringify(payload) + '\n');
  process.exit(code);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function main() {
  const args = parseArgs();
  if (!args.body) {
    jsonOut({ error: '--body is required' }, 1);
  }

  const cdpUrl = `http://localhost:${args.port}`;
  let browser;
  try {
    browser = await chromium.connectOverCDP(cdpUrl, { timeout: 15000 });
  } catch (e) {
    // AuthFailure pattern so dispatcher raises AuthFailure and pauses channel
    process.stderr.write(`Fatal: could not connect to CDP at ${cdpUrl}: ${e.message}\n`);
    process.exit(1);
  }

  const ctx = browser.contexts()[0];
  const page = ctx.pages()[0] || await ctx.newPage();

  // Navigate to LinkedIn feed
  try {
    await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  } catch (e) {
    process.stderr.write(`Fatal: goto linkedin.com/feed timeout: ${e.message}\n`);
    await browser.close();
    process.exit(1);
  }
  await sleep(3000);

  const currentUrl = page.url();
  console.log('URL:', currentUrl);

  // Detect login wall
  if (currentUrl.includes('/login') || currentUrl.includes('/authwall') || currentUrl.includes('/checkpoint')) {
    process.stderr.write(`Fatal: linkedin login required — session not authenticated. URL: ${currentUrl}\n`);
    await browser.close();
    process.exit(1);
  }

  if (args.dryRun) {
    console.log('[dry-run] would post:', args.body.slice(0, 120));
    await browser.close();
    jsonOut({ status: 'dry-run', body_preview: args.body.slice(0, 120) });
  }

  // Remove overlays
  await page.evaluate(() => {
    document.querySelectorAll('[id*="artdeco-global-alert"], [class*="global-alert"], .artdeco-modal').forEach(e => e.remove());
    document.querySelectorAll('[class*="cookie"], [class*="privacy"], [class*="consent"]').forEach(e => e.remove());
  });
  await sleep(500);

  // Click "Start a post"
  const btn = page.locator('button:has-text("Start a post")').first();
  try {
    await btn.waitFor({ state: 'visible', timeout: 20000 });
    await btn.dispatchEvent('click');
  } catch (e) {
    process.stderr.write(`Fatal: locator.dispatchEvent: ${e.message}\n`);
    await browser.close();
    process.exit(1);
  }
  console.log('Clicked Start a post');
  await sleep(3000);

  // Fill editor
  const editor = page.locator('[contenteditable="true"]').first();
  const editorCount = await editor.count();
  if (editorCount === 0) {
    process.stderr.write('Fatal: no editor appeared after clicking Start a post\n');
    await browser.close();
    process.exit(1);
  }

  await editor.click();
  // Type human-like to avoid bot detection
  for (const ch of args.body) {
    await page.keyboard.type(ch, { delay: 20 + Math.floor(Math.random() * 60) });
  }
  console.log('Filled post text:', args.body.slice(0, 80));
  await sleep(1500);

  // Click Post button
  const postBtn = page.locator([
    'button.share-actions__primary-action',
    'button[aria-label="Post"]',
    '.share-box-feed-entry__footer button.artdeco-button--primary',
    'button.artdeco-button--primary:has-text("Post")',
  ].join(', ')).first();

  try {
    await postBtn.waitFor({ state: 'visible', timeout: 15000 });
    await postBtn.click();
  } catch (e) {
    process.stderr.write(`Fatal: could not click Post button: ${e.message}\n`);
    await browser.close();
    process.exit(1);
  }
  console.log('Clicked Post');
  await sleep(4000);

  const finalUrl = page.url();
  console.log('Final URL:', finalUrl);
  await browser.close();

  jsonOut({ status: 'sent', body_preview: args.body.slice(0, 120), final_url: finalUrl });
}

main().catch(err => {
  process.stderr.write(`Fatal: ${err.message}\n`);
  process.exit(1);
});
