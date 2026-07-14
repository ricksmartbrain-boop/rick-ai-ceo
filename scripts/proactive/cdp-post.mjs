#!/usr/bin/env node
/**
 * cdp-post.mjs — Post to social platforms via Chrome CDP (Playwright)
 * 
 * Usage:
 *   node cdp-post.mjs --port 9225 --platform linkedin --text "post content"
 *   node cdp-post.mjs --port 9222 --platform threads --text "post content"
 *   node cdp-post.mjs --port 9223 --platform reddit --text "post content" --subreddit SaaS
 */

import { chromium } from 'playwright';

const args = process.argv.slice(2);
function getArg(name) {
  const idx = args.indexOf(`--${name}`);
  return idx >= 0 ? args[idx + 1] : null;
}

const port = getArg('port') || '9225';
const platform = getArg('platform') || 'linkedin';
const text = getArg('text');
const subreddit = getArg('subreddit') || 'SaaS';
const imagePath = getArg('image');

if (!text && platform !== 'instagram') {
  console.error('Error: --text is required');
  process.exit(1);
}
if (platform === 'instagram' && !imagePath) {
  console.error('Error: --image is required for instagram (IG web cannot post text-only)');
  process.exit(1);
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

// Safe click using dispatchEvent (NOT elementHandle.click())
async function safeClick(page, selector, description) {
  const el = await page.$(selector);
  if (!el) {
    console.log(`[WARN] ${description}: selector not found: ${selector}`);
    return false;
  }
  await el.evaluate(e => e.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })));
  console.log(`[OK] Clicked: ${description}`);
  await sleep(1500);
  return true;
}

async function postLinkedIn(page) {
  console.log('[LinkedIn] Navigating to feed...');
  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(3500);

  // Open the composer. The "Start a post" trigger is rendered as an <a>, not a
  // <button>, in the current LinkedIn UI, so match across a/button/role=button
  // and click via real mouse coordinates.
  const triggerBox = await page.evaluate(() => {
    const el = Array.from(document.querySelectorAll('a, button, [role="button"]'))
      .find(e => /start a post/i.test((e.textContent || '').trim()));
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  if (!triggerBox) {
    console.error('[ERROR] LinkedIn "Start a post" trigger not found');
    process.exit(1);
  }
  await page.mouse.click(triggerBox.x, triggerBox.y);
  console.log('[OK] Opened composer');
  await sleep(5000);

  // The composer auto-focuses its contenteditable editor on open. The editor
  // node is obfuscated and not reliably matchable by selector, so type directly
  // via the keyboard (do NOT click first — a stray click dismisses the dialog).
  await page.keyboard.type(text, { delay: 12 });
  console.log('[OK] Text entered');
  await sleep(1500);

  // Click Post via role locator (button label is nested in a span).
  const postBtn = page.getByRole('button', { name: 'Post', exact: true });
  const count = await postBtn.count();
  for (let i = 0; i < count; i++) {
    const b = postBtn.nth(i);
    if (await b.isVisible().catch(() => false) && await b.isEnabled().catch(() => false)) {
      await b.click({ timeout: 10000 });
      console.log('[OK] Posted on LinkedIn');
      await sleep(3000);
      return true;
    }
  }
  console.error('[ERROR] LinkedIn Post button not found or not enabled');
  return false;
}

async function postThreads(page) {
  console.log('[Threads] Navigating...');
  await page.goto('https://www.threads.net/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(3000);

  // Threads now renders a feed composer bar instead of a modal in many sessions.
  // Prefer the explicit composer button, then fall back to the visible textbox.
  const composeButton = page.locator(
    'div[role="button"][aria-label="Empty text field. Type to compose a new post."], ' +
    'div[role="button"]:has-text("What\'s new?"), ' +
    '[aria-label*="compose" i]'
  ).first();

  if (await composeButton.count()) {
    await composeButton.click({ timeout: 10000 }).catch(async () => {
      const box = await composeButton.boundingBox().catch(() => null);
      if (!box) throw new Error('composer button not clickable');
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    });
    console.log('[OK] Opened Threads composer');
    await sleep(1000);
  }

  const editor = page.locator(
    'div[role="textbox"][aria-label="Empty text field. Type to compose a new post."], ' +
    '[role="textbox"][aria-label*="compose" i], ' +
    'div[contenteditable="true"][aria-label*="compose" i], ' +
    '[contenteditable="true"]'
  ).first();

  await editor.waitFor({ state: 'visible', timeout: 15000 });
  await editor.click({ timeout: 10000 });
  await sleep(300);
  await page.keyboard.type(text, { delay: 10 });
  console.log('[OK] Text entered');
  await sleep(1000);

  // Click the topmost visible Post button.
  const postButtons = await page.locator('div[role="button"], button').filter({ hasText: /^Post$/ }).evaluateAll(els =>
    els.map(el => {
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height, text: (el.textContent || '').trim() };
    }).filter(btn => btn.w > 0 && btn.h > 0)
  );

  if (!postButtons.length) {
    console.error('[ERROR] Threads Post button not found');
    return false;
  }

  postButtons.sort((a, b) => a.y - b.y);
  const postBtn = postButtons[0];
  await page.mouse.click(postBtn.x + postBtn.w / 2, postBtn.y + postBtn.h / 2);
  console.log('[OK] Clicked Post');
  await sleep(3000);
  return true;
}

async function postReddit(page, sub) {
  console.log(`[Reddit] Navigating to r/${sub}...`);
  await page.goto(`https://old.reddit.com/r/${sub}/`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(3000);
  
  // Find top post and reply
  const links = await page.$$('a.title');
  if (links.length === 0) {
    console.error('[ERROR] No posts found');
    return false;
  }
  
  // Click first post
  await links[0].evaluate(e => e.dispatchEvent(new MouseEvent('click', { bubbles: true })));
  await sleep(3000);
  
  // Find comment box
  const commentBox = await page.$('textarea[name="text"], .commentarea textarea');
  if (!commentBox) {
    console.error('[ERROR] Comment box not found');
    return false;
  }
  await commentBox.evaluate((el, txt) => { el.value = txt; el.dispatchEvent(new Event('input', { bubbles: true })); }, text);
  await sleep(1000);
  
  const saveBtn = await page.$('button[type="submit"].save, button:has-text("save"), input[type="submit"][value="save"]');
  if (saveBtn) {
    await saveBtn.evaluate(e => e.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    console.log('[OK] Comment posted on Reddit');
    await sleep(2000);
    return true;
  }
  console.error('[ERROR] Reddit save button not found');
  return false;
}

async function postInstagram(page) {
  console.log('[Instagram] Navigating...');
  await page.goto('https://www.instagram.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(4000);

  // Open the Create dialog via the sidebar 'New post' svg. The clickable element is
  // an ancestor of the svg, so walk up to the nearest role=button/link.
  const clickedCreate = await page.evaluate(() => {
    const cand = document.querySelector('svg[aria-label="New post"], svg[aria-label="Create"]');
    if (!cand) return false;
    let el = cand;
    for (let i = 0; i < 6 && el; i++) {
      if (el.getAttribute && (el.getAttribute('role') === 'button' || el.tagName === 'A')) break;
      el = el.parentElement;
    }
    (el || cand).dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    return true;
  });
  console.log(clickedCreate ? '[OK] Clicked Create' : '[WARN] Create control not found');
  await sleep(2500);

  // Create opens a submenu with 'Post' and 'AI'. Click the 'Post' item to reach upload.
  const clickedPost = await page.evaluate(() => {
    const items = Array.from(document.querySelectorAll('[role="menuitem"], a[role="link"], [role="button"], span, div'));
    // Find an element whose trimmed text is exactly 'Post' and is reasonably small.
    const target = items.find(el => {
      const t = (el.textContent || '').trim();
      return t === 'Post' && el.getBoundingClientRect().width > 0;
    });
    if (target) { target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })); return true; }
    return false;
  });
  console.log(clickedPost ? '[OK] Clicked Post submenu' : '[INFO] No Post submenu (single-step flow)');
  await sleep(2000);

  // Upload the image via the file input.
  const fileInput = page.locator('input[type="file"]').first();
  await fileInput.waitFor({ state: 'attached', timeout: 15000 });
  await fileInput.setInputFiles(imagePath);
  console.log('[OK] Image selected');
  await sleep(3000);

  // Click Next twice (crop -> filter -> caption).
  for (let i = 0; i < 2; i++) {
    const next = page.locator('div[role="button"]:has-text("Next"), button:has-text("Next")').first();
    if (await next.count()) {
      await next.click({ timeout: 8000 }).catch(() => {});
      console.log(`[OK] Next ${i + 1}`);
      await sleep(2000);
    }
  }

  // Enter caption.
  if (text) {
    const caption = page.locator('div[aria-label="Write a caption..."][contenteditable="true"], textarea[aria-label="Write a caption..."], div[contenteditable="true"]').first();
    if (await caption.count()) {
      await caption.click({ timeout: 8000 }).catch(() => {});
      await sleep(300);
      await page.keyboard.type(text, { delay: 8 });
      console.log('[OK] Caption entered');
      await sleep(1000);
    }
  }

  // Share.
  const share = page.locator('div[role="button"]:has-text("Share"), button:has-text("Share")').first();
  if (await share.count()) {
    await share.click({ timeout: 10000 }).catch(() => {});
    console.log('[OK] Clicked Share');
    await sleep(6000);
    return true;
  }
  console.error('[ERROR] Instagram Share button not found');
  return false;
}

async function main() {
  let browser;
  try {
    browser = await chromium.connectOverCDP(`http://localhost:${port}`);
    const contexts = browser.contexts();
    const context = contexts[0] || await browser.newContext();
    const pages = context.pages();
    const page = pages[0] || await context.newPage();

    let success = false;
    switch (platform) {
      case 'linkedin':
        success = await postLinkedIn(page);
        break;
      case 'threads':
        success = await postThreads(page);
        break;
      case 'reddit':
        success = await postReddit(page, subreddit);
        break;
      case 'instagram':
        success = await postInstagram(page);
        break;
      default:
        console.error(`Unknown platform: ${platform}`);
        process.exit(1);
    }

    process.exit(success ? 0 : 1);
  } catch (err) {
    console.error(`[ERROR] ${err.message}`);
    process.exit(1);
  }
}

main();
