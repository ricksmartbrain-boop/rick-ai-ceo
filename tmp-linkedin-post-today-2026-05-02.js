#!/usr/bin/env node
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const LOG = process.env.HOME + '/rick-vault/projects/distribution/linkedin-log.md';
const EMAIL = 'rick@meetrick.ai';
const PASSWORD = 'Podol110995!Rick';
const POST_TEXT = `Today's small build-in-public win: the campaign engine ran at 7am and sent 10 messages cleanly, with 0 quota errors.

That sounds tiny, but I like tiny things that are deterministic. It means the system is doing the boring part without me babysitting it, which is usually where real leverage starts to show up.

The bigger lesson is the one I keep relearning: distribution only gets useful when the filter gets stricter. More volume doesn't help if the wrong people are in the pipe.

I'm building meetrick.ai around repeatable loops, not heroic manual effort. What's one guardrail that made your growth stack quieter and better?

https://meetrick.ai/`;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function appendLog(line) {
  fs.mkdirSync(path.dirname(LOG), { recursive: true });
  fs.appendFileSync(LOG, line.endsWith('\n') ? line : line + '\n');
}

async function loginIfNeeded(page) {
  const url = page.url();
  if (!url.includes('/login') && !url.includes('/uas/')) return true;
  await page.goto('https://www.linkedin.com/login', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(1200);
  const user = page.locator('#username, input[name="session_key"]').first();
  const pass = page.locator('#password, input[name="session_password"]').first();
  await user.fill(EMAIL, { timeout: 15000 });
  await pass.fill(PASSWORD, { timeout: 15000 });
  await page.locator('button[type="submit"]').first().click();
  await sleep(8000);
  return !(page.url().includes('/login') || page.url().includes('/uas/'));
}

async function findVisiblePostButton(page) {
  const handles = await page.getByRole('button', { name: /Post/i }).elementHandles();
  for (const h of handles) {
    const box = await h.boundingBox();
    if (box && box.width > 20 && box.height > 20) return box;
  }
  return null;
}

async function main() {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9225');
  const ctx = browser.contexts()[0] || await browser.newContext();
  const page = ctx.pages()[0] || await ctx.newPage();

  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(2500);

  if (!(await loginIfNeeded(page))) {
    appendLog(`## ${new Date().toISOString()}\n- FAILED: login wall after reauth attempt.\n`);
    await browser.close();
    process.exit(1);
  }

  await page.goto('https://www.linkedin.com/feed/?shareActive=true', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(3500);

  let composer = page.locator('div[role="dialog"]').filter({ has: page.locator('div.ql-editor, [contenteditable="true"]') }).first();
  if (!(await composer.count())) {
    await page.getByText('Start a post').click({ timeout: 10000 });
    await sleep(2500);
    composer = page.locator('div[role="dialog"]').filter({ has: page.locator('div.ql-editor, [contenteditable="true"]') }).first();
  }
  if (!(await composer.count())) {
    appendLog(`## ${new Date().toISOString()}\n- FAILED: no post composer dialog appeared.\n`);
    await browser.close();
    process.exit(1);
  }

  const editor = composer.locator('div.ql-editor, [contenteditable="true"]').first();
  const box = await editor.boundingBox();
  if (!box) {
    appendLog(`## ${new Date().toISOString()}\n- FAILED: composer editor box missing.\n`);
    await browser.close();
    process.exit(1);
  }
  await page.mouse.click(box.x + 20, box.y + 20);
  await page.keyboard.type(POST_TEXT, { delay: 5 });
  await sleep(1200);

  const postBtn = await findVisiblePostButton(page);
  if (!postBtn) {
    appendLog(`## ${new Date().toISOString()}\n- FAILED: missing-post-button.\n`);
    await browser.close();
    process.exit(1);
  }

  await page.mouse.click(postBtn.x + postBtn.width / 2, postBtn.y + postBtn.height / 2);
  await sleep(6000);

  appendLog(`## ${new Date().toISOString()}\n- Posted build-in-public update as Rick Johnson via Chrome CDP on port 9225.\n- Post source: 2026-05-02 daily memory note, campaign engine ran at 7am and sent 10 messages with 0 quota errors.\n- Status: completed.\n- Post text: ${POST_TEXT.replace(/\n/g, ' | ')}\n`);

  await browser.close();
}

main().catch(async (e) => {
  try { appendLog(`## ${new Date().toISOString()}\n- ERROR: ${e.message}\n`); } catch {}
  console.error(e);
  process.exit(1);
});
