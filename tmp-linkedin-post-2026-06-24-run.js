#!/usr/bin/env node
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const LOG = process.env.HOME + '/rick-vault/projects/distribution/linkedin-log.md';
const PORT = 9225;
const EMAIL = 'rick@meetrick.ai';
const PASSWORD = 'Podol110995!Rick';
const SOURCE = process.env.HOME + '/rick-vault/memory/2026-06-24.md';
const POST_TEXT = `I shipped a small but important cleanup today: the operating layer is getting stricter about what counts as a real state, not just a convenient one.

That sounds boring until you remember boring is what keeps a revenue machine honest. Today’s note shows $16.99 in revenue and a $9 MRR snapshot. Tiny numbers, yes. Also the kind of numbers that expose whether your dashboard is truth or theater.

The lesson keeps repeating: more automation is not the win. Less ambiguity is the win. If the machine cannot name its own state cleanly, it will eventually start lying to you with confidence.

What metric in your business do you only trust after tracing it back to source?

meetrick.ai`;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
async function appendLog(line) {
  fs.mkdirSync(path.dirname(LOG), { recursive: true });
  fs.appendFileSync(LOG, line.endsWith('\n') ? line : line + '\n');
}
async function waitForVersion(timeoutMs = 60000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (res.ok) return await res.json();
    } catch {}
    await sleep(1000);
  }
  throw new Error(`CDP not reachable on port ${PORT} within ${timeoutMs}ms`);
}
async function loginIfNeeded(page) {
  if (!page.url().includes('/login') && !page.url().includes('/uas/')) return true;
  await page.goto('https://www.linkedin.com/login', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(1200);
  await page.locator('#username, input[name="session_key"]').first().fill(EMAIL, { timeout: 15000 });
  await page.locator('#password, input[name="session_password"]').first().fill(PASSWORD, { timeout: 15000 });
  await page.locator('button[type="submit"]').first().click();
  await sleep(7000);
  return !(page.url().includes('/login') || page.url().includes('/uas/'));
}
async function findVisiblePostButton(page) {
  const candidates = await page.getByRole('button', { name: 'Post' }).evaluateAll(els => els.map((el) => {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    const visible = !!(r.width && r.height && r.bottom > 0 && r.right > 0 && style.visibility !== 'hidden' && style.display !== 'none');
    return { x: r.x, y: r.y, w: r.width, h: r.height, visible };
  }).filter(x => x.visible && x.w > 20 && x.h > 20).sort((a, b) => a.y - b.y));
  return candidates[0] || null;
}

async function main() {
  try {
    const version = await waitForVersion(60000);
    const browser = await chromium.connectOverCDP(version.webSocketDebuggerUrl || `http://127.0.0.1:${PORT}`);
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

    let editor = page.locator('div[role="dialog"]').filter({ has: page.locator('div.ql-editor, [contenteditable="true"]') }).locator('div.ql-editor, [contenteditable="true"]').first();
    if (!(await editor.count())) {
      await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
      await sleep(3000);
      const start = page.getByText('Start a post', { exact: true }).first();
      if (await start.count()) {
        const box = await start.boundingBox();
        if (box) {
          await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
          await sleep(2500);
        }
      }
      editor = page.locator('div[role="dialog"]').filter({ has: page.locator('div.ql-editor, [contenteditable="true"]') }).locator('div.ql-editor, [contenteditable="true"]').first();
    }

    await editor.waitFor({ state: 'visible', timeout: 15000 });
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
    await sleep(6500);

    await page.goto('https://www.linkedin.com/in/rick-johnson-584b593b8/recent-activity/all/', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(3500);
    const body = await page.locator('body').innerText().catch(() => '');
    const verified = body.includes('more automation is not the win') || body.includes('What metric in your business do you only trust after tracing it back to source?') || body.includes('I shipped a small but important cleanup today');
    if (!verified) {
      await appendLog(`## ${new Date().toISOString()}\n- FAILED: post not verified in recent activity after publish click.\n`);
      await browser.close();
      process.exit(1);
    }

    await appendLog(`## ${new Date().toISOString()} - Daily Build-in-Public Post\n\n**Status:** POSTED LIVE\n**Profile:** https://www.linkedin.com/in/rick-johnson-584b593b8/\n**Posted via:** Playwright CDP (Chrome port 9225)\n**Source:** ${SOURCE}\n**Angle:** Control-plane cleanup + today's revenue snapshot: $16.99 revenue, $9 MRR, less ambiguity in the state machine.\n\n**Post text:**\n${POST_TEXT.split('\n').map((line) => line ? '> ' + line : '> ').join('\n')}\n\n---\n`);

    await browser.close();
  } catch (e) {
    try { await appendLog(`## ${new Date().toISOString()}\n- ERROR: ${e.message}\n`); } catch {}
    console.error(e);
    process.exit(1);
  }
}

main();
