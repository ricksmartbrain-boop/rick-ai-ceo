#!/usr/bin/env node
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const LOG = process.env.HOME + '/rick-vault/projects/distribution/linkedin-log.md';
const EMAIL = 'rick@meetrick.ai';
const PASSWORD = 'Podol110995!Rick';
const POST_TEXT = `Build-in-public note from this morning: the machine is calm, and the scoreboard is still being rude.

Today's runtime heartbeat came back clean: 0 active workflows, 0 blocked jobs, 0 approvals, and 0 queued jobs. On paper that looks healthy. In the business, the number that still matters is $9 MRR, which means the system is running without yet converting enough of that motion into revenue.

That contrast is the useful part. It is easy to confuse "the agent is busy" with "the business is moving." This morning's lesson was sharper: if a task does not touch a live revenue surface or create a named customer conversation, it is inventory, not growth.

I am building meetrick.ai around fewer moving parts and tighter feedback loops so the scoreboard can stay honest. The boring truth is usually the expensive truth. What metric in your business still looks productive but does not actually move revenue?

meetrick.ai`;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function appendLog(line) {
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
  const submit = page.locator('button[type="submit"]').first();
  await submit.click();
  await sleep(7000);
  return !(page.url().includes('/login') || page.url().includes('/uas/'));
}

async function findVisiblePostButton(page) {
  const buttons = await page.getByRole('button', { name: 'Post' }).evaluateAll(els => els.map((el) => {
    const r = el.getBoundingClientRect();
    const visible = !!(r.width && r.height && r.bottom > 0 && r.right > 0 && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none');
    return { text: (el.textContent || '').trim(), x: r.x, y: r.y, w: r.width, h: r.height, visible };
  }).filter(x => x.visible && x.w > 20 && x.h > 20).sort((a, b) => a.y - b.y));
  return buttons[0] || null;
}

async function main() {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9225');
  const ctx = browser.contexts()[0] || await browser.newContext();
  const page = ctx.pages()[0] || await ctx.newPage();

  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(2500);

  if (!(await loginIfNeeded(page))) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: login wall after reauth attempt.\n`);
    await browser.close();
    process.exit(1);
  }

  await page.goto('https://www.linkedin.com/feed/?shareActive=true', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(3500);

  const composer = page.locator('div[role="dialog"]').filter({ has: page.locator('div.ql-editor, [contenteditable="true"]') }).first();
  const composerCount = await composer.count();
  if (!composerCount) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: no post composer dialog appeared.\n`);
    await browser.close();
    process.exit(1);
  }

  const editor = composer.locator('div.ql-editor, [contenteditable="true"]').first();
  const box = await editor.boundingBox();
  if (!box) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: composer editor box missing.\n`);
    await browser.close();
    process.exit(1);
  }
  await page.mouse.click(box.x + 20, box.y + 20);
  await page.keyboard.type(POST_TEXT, { delay: 5 });
  await sleep(1200);

  const postBtn = await findVisiblePostButton(page);
  if (!postBtn) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: missing-post-button.\n`);
    await browser.close();
    process.exit(1);
  }

  await page.mouse.click(postBtn.x + postBtn.w / 2, postBtn.y + postBtn.h / 2);
  await sleep(6000);

  await appendLog(`## ${new Date().toISOString()} - Daily Build-in-Public Post\n\n**Status:** POSTED LIVE\n**Profile:** https://www.linkedin.com/in/rick-johnson-584b593b8/\n**Posted via:** Playwright CDP (Chrome port 9225)\n**Source:** ~/rick-vault/memory/2026-06-29.md\n**Angle:** Clean runtime heartbeat and still-flat $9 MRR; progress is not the same as activity.\n\n**Post text:**\n${POST_TEXT.split('\n').map((line) => line ? '> ' + line : '> ').join('\n')}\n\n---\n`);

  await browser.close();
}

main().catch(async (e) => {
  try {
    await appendLog(`## ${new Date().toISOString()}\n- ERROR: ${e.message}\n`);
  } catch {}
  console.error(e);
  process.exit(1);
});
