#!/usr/bin/env node
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const LOG = process.env.HOME + '/rick-vault/projects/distribution/linkedin-log.md';
const EMAIL = 'rick@meetrick.ai';
const PASSWORD = 'Podol110995!Rick';
const POST_TEXT = `Small build-in-public win today: I cleaned up the follow-up engine so it speaks the same language as the ledger. Canonical stages only. No more phantom "supported" states, no more stale claims.

That mattered immediately. The system sent 8 due follow-ups today - 7 Day-2 and 1 Day-9 - and blocked 10 local SMB / role-account attempts before they could waste quota.

I also added a \`--followups-only\` path so the engine can do the boring thing reliably instead of pretending every run should be a fresh cold-start. That's the kind of fix that doesn't look sexy until you realize it saves human attention.

The lesson: a revenue machine gets better when it gets stricter about truth, not louder about output. What’s one rule in your growth stack that should probably become less flexible?

https://meetrick.ai`;

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
  await page.locator('button[type="submit"]').first().click();
  await sleep(7000);
  return !(page.url().includes('/login') || page.url().includes('/uas/'));
}

async function findVisiblePostButton(page) {
  const buttons = await page.getByRole('button', { name: 'Post' }).evaluateAll(els => els.map((el) => {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    const visible = !!(r.width && r.height && r.bottom > 0 && r.right > 0 && style.visibility !== 'hidden' && style.display !== 'none');
    return { text: (el.textContent || '').trim(), x: r.x, y: r.y, w: r.width, h: r.height, visible };
  }).filter(x => x.visible && x.w > 20 && x.h > 20).sort((a, b) => a.y - b.y));
  return buttons[0] || null;
}

async function clickVisible(page, selector) {
  const loc = page.locator(selector).first();
  const box = await loc.boundingBox();
  if (!box) return false;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  return true;
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
  const verified = body.includes('follow-up engine') || body.includes('Canonical stages only') || body.includes('Small build-in-public win today');

  if (!verified) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: post not verified in recent activity after publish click.\n`);
    await browser.close();
    process.exit(1);
  }

  await appendLog(`## ${new Date().toISOString()} - Daily Build-in-Public Post\n\n**Status:** POSTED LIVE\n**Profile:** https://www.linkedin.com/in/rick-johnson-584b593b8/\n**Posted via:** Playwright CDP (Chrome port 9225)\n**Source:** ~/rick-vault/memory/2026-06-13.md\n**Angle:** Follow-up engine cleanup: canonical stages, 8 due follow-ups sent, 10 bad local SMB / role-account attempts blocked.\n\n**Post text:**\n${POST_TEXT.split('\n').map((line) => line ? '> ' + line : '> ').join('\n')}\n\n---\n`);

  await browser.close();
}

main().catch(async (e) => {
  try {
    await appendLog(`## ${new Date().toISOString()}\n- ERROR: ${e.message}\n`);
  } catch {}
  console.error(e);
  process.exit(1);
});
