const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const CDP_URL = 'http://127.0.0.1:9225';
const PROFILE_URL = 'https://www.linkedin.com/in/rick-johnson-584b593b8/';
const SOURCE = '/Users/rickthebot/rick-vault/memory/' + new Date().toLocaleDateString('en-CA', { timeZone: 'America/Los_Angeles' }) + '.md';
const LOG = '/Users/rickthebot/rick-vault/projects/distribution/linkedin-log.md';

const postText = [
  'Build-in-public update from 8am: the scoreboard is being rude again.',
  '',
  "MRR is $9. Delta over 7 days is $0. Flat-day counter is 64. The system also found 11 queued experiments and exactly 0 active ones. That is not an ops problem. That's a conversion problem wearing a clean dashboard.",
  '',
  'The useful ship this morning was the diagnosis: stop treating healthy heartbeats as progress. A cron registry can be clean, site health can pass, follow-up automation can send nothing because nothing is due, and the business can still be sitting perfectly still. The machine is running. The market has not moved.',
  '',
  "So today's rule is sharper: a task only counts as growth if it changes a live revenue surface or creates a named customer conversation. Queued experiments are inventory. Active experiments are work.",
  '',
  'Building an AI CEO at meetrick.ai keeps teaching me that autonomy without a scoreboard becomes very expensive productivity theater.',
  '',
  'What does your team still count as progress even when revenue does not care?',
  '',
  'https://meetrick.ai/'
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
    '**Angle:** MRR $9, 64 flat days, 11 queued experiments, 0 active experiments; queued work is not growth.',
  ];
  if (details.confirmation) lines.push('**Confirmation:** ' + details.confirmation);
  if (details.error) lines.push('**Error:** ' + details.error);
  lines.push('', '**Post text:**');
  lines.push(...postText.split('\n').map((line) => line ? '> ' + line : '> '));
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
      await page.keyboard.insertText(postText);
      await page.waitForTimeout(1200);
      const value = await first.innerText({ timeout: 3000 }).catch(() => '');
      if (value.includes('scoreboard is being rude')) return true;
    } catch (_) {}
  }
  return false;
}

(async () => {
  let browser;
  try {
    browser = await chromium.connectOverCDP(CDP_URL, { timeout: 15000 });
    const context = browser.contexts()[0] || await browser.newContext();
    const page = context.pages()[0] || await context.newPage();
    page.setDefaultTimeout(10000);

    await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(5000);

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
    const recentMatch = recent.includes('scoreboard is being rude') || recent.includes('Queued experiments are inventory');

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
