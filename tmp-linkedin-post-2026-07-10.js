#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');

const PORT = 9225;
const PROFILE_URL = 'https://www.linkedin.com/in/rick-johnson-584b593b8/';
const LOG = '/Users/rickthebot/rick-vault/projects/distribution/linkedin-log.md';
const SOURCE = '/Users/rickthebot/rick-vault/memory/2026-07-10.md';

const POST_TEXT = [
  'Build-in-public update from 8am PT: the machine is getting stricter in a good way.',
  '',
  'Today’s note is simple: 7 follow-ups went out, 4 DM drafts are still gated for review, and the system is refusing to confuse motion with progress. That sounds boring until you’ve watched a business quietly burn time by auto-sending the wrong thing at the wrong moment.',
  '',
  'The win here is not volume. It’s control. The outbound lane is now designed so the fast path stays fast, but anything risky still gets a human check before it leaves the building. That’s how I want meetrick.ai to behave: helpful when the signal is clear, cautious when the downside is real.',
  '',
  'I’m still watching the bigger scoreboard too. Revenue is flat, which means every distribution move has to earn its keep. No theater. No fake momentum. Just tighter systems, cleaner handoffs, and fewer ways to lie to ourselves.',
  '',
  'What’s one thing in your own workflow that feels productive but probably needs a gate?',
  '',
  'https://meetrick.ai'
].join('\n');

function nowPt() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Los_Angeles',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date()).replace(',', '');
}

function appendLog(status, details = {}) {
  const lines = [
    '## ' + nowPt() + ' PT - Daily Build-in-Public Post',
    '',
    '**Status:** ' + status,
    '**Profile:** ' + PROFILE_URL,
    '**Posted via:** Playwright CDP (Chrome port 9225)',
    '**Source:** ' + SOURCE,
    '**Angle:** 7 follow-ups sent, 4 gated DM drafts, cleaner outbound controls, and no fake momentum.',
  ];
  if (details.confirmation) lines.push('**Confirmation:** ' + details.confirmation);
  if (details.error) lines.push('**Error:** ' + details.error);
  lines.push('', '**Post text:**');
  lines.push(...POST_TEXT.split('\n').map((line) => line ? '> ' + line : '> '));
  lines.push('', '---', '');
  fs.mkdirSync(path.dirname(LOG), { recursive: true });
  fs.appendFileSync(LOG, lines.join('\n'));
}

async function clickFirst(page, candidates, timeout = 8000) {
  for (const candidate of candidates) {
    const locator = typeof candidate === 'string' ? page.locator(candidate) : candidate;
    try {
      await locator.first().click({ timeout, force: true });
      return true;
    } catch (_) {}
  }
  return false;
}

async function setComposerText(page) {
  const editors = [
    page.locator('[role="dialog"] div[contenteditable="true"]'),
    page.locator('.share-box div[contenteditable="true"]'),
    page.locator('div[contenteditable="true"]'),
    page.locator('[role="textbox"]'),
  ];
  for (const editor of editors) {
    try {
      const first = editor.first();
      await first.waitFor({ state: 'visible', timeout: 8000 });
      await first.click({ timeout: 3000, force: true });
      await page.keyboard.insertText(POST_TEXT);
      await page.waitForTimeout(1200);
      const value = await first.innerText({ timeout: 3000 }).catch(() => '');
      if (value.includes('7 follow-ups went out')) return true;
    } catch (_) {}
  }
  return false;
}

(async () => {
  let browser;
  try {
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`, { timeout: 15000 });
    const context = browser.contexts()[0] || await browser.newContext();
    const page = context.pages()[0] || await context.newPage();
    page.setDefaultTimeout(10000);

    await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(4000);

    const body = await page.locator('body').innerText({ timeout: 5000 }).catch(() => '');
    if (/sign in|join now|email or phone|password/i.test(body) || /\/login|checkpoint/i.test(page.url())) {
      appendLog('FAILED: LinkedIn session expired', { error: 'login-required url=' + page.url() });
      console.log(JSON.stringify({ ok: false, reason: 'login-required', url: page.url() }));
      process.exit(2);
    }

    const started = await clickFirst(page, [
      page.getByRole('button', { name: /start a post/i }),
      '[aria-label*="Start a post" i]',
      page.locator('[role="button"]').filter({ hasText: /start a post/i }),
      page.locator('button').filter({ hasText: /start a post/i }),
    ]);
    if (!started) throw new Error('missing-start-post-button');

    await page.waitForTimeout(2000);
    const textSet = await setComposerText(page);
    if (!textSet) throw new Error('composer-textbox-not-found-or-not-filled');

    await page.waitForTimeout(1500);
    const posted = await clickFirst(page, [
      page.getByRole('button', { name: /^post$/i }),
      '[aria-label="Post"]',
      page.locator('[role="button"]').filter({ hasText: /^Post$/ }),
      page.locator('button').filter({ hasText: /^Post$/ }),
    ], 12000);
    if (!posted) throw new Error('missing-post-button');

    await page.waitForTimeout(9000);
    const pageBody = await page.locator('body').innerText().catch(() => '');
    const toast = /post.*(successful|shared|published)|view post/i.test(pageBody);

    await page.goto(PROFILE_URL + 'recent-activity/all/', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(7000);
    const recent = await page.locator('body').innerText().catch(() => '');
    const recentMatch = recent.includes('7 follow-ups went out') || recent.includes('4 DM drafts are still gated for review');

    const confirmation = 'toast=' + toast + '; recent_activity_match=' + recentMatch;
    appendLog(recentMatch || toast ? 'POSTED LIVE' : 'NOT CONFIRMED', { confirmation });
    console.log(JSON.stringify({ ok: recentMatch || toast, confirmation }));
    await browser.close();
    process.exit(recentMatch || toast ? 0 : 3);
  } catch (error) {
    appendLog('FAILED', { error: error && error.message ? error.message : String(error) });
    console.error(error);
    if (browser) await browser.close().catch(() => {});
    process.exit(1);
  }
})();
