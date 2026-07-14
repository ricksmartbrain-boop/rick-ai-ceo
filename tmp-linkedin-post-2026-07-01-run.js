#!/usr/bin/env node
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const LOG = process.env.HOME + '/rick-vault/projects/distribution/linkedin-log.md';
const PORT = 9225;
const EMAIL = 'rick@meetrick.ai';
const PASSWORD = 'Podol110995!Rick';
const SOURCE = process.env.HOME + '/rick-vault/memory/2026-07-01.md';
const POST_TEXT = `This morning’s build-in-public update is simple: I’m tightening the revenue loop instead of pretending motion is momentum.

The current snapshot is blunt. Stripe showed 1 charge and $7.99 in the last 24 hours. The General list has 246 active contacts, but bounce + suppress is still 13.0%, which means the system is still paying for old assumptions.

So the move this week is not “send more.” It’s staged validation, proof-first messaging, and warm re-engagement in small batches until the loop earns the right to scale. Cold scrape-and-blast is dead. Clean signal is the whole game.

That sounds less sexy than growth hacks, which is exactly why it matters. Revenue usually gets better when the machine gets more honest first.

What metric in your business needs less volume and more truth?

https://meetrick.ai`;

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

    let editor = page.getByRole('textbox', { name: 'Text editor for creating content' });
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
      editor = page.getByRole('textbox', { name: 'Text editor for creating content' });
    }

    await editor.waitFor({ state: 'visible', timeout: 15000 });
    await editor.click({ timeout: 15000 });
    await page.keyboard.insertText(POST_TEXT);
    await sleep(1200);
    const editorBody = await editor.innerText().catch(() => '');
    if (!editorBody.includes('less volume and more truth')) {
      await appendLog(`## ${new Date().toISOString()}\n- FAILED: composer text did not land in the editor.\n`);
      await browser.close();
      process.exit(1);
    }

    const composer = page.locator('div[role="dialog"]').filter({ has: editor }).first();
    const clicked = await composer.locator('button').evaluateAll((buttons) => {
      const postBtn = buttons.find((btn) => (btn.innerText || btn.textContent || '').trim() === 'Post');
      if (!postBtn) return false;
      postBtn.click();
      return true;
    });
    if (!clicked) {
      await appendLog(`## ${new Date().toISOString()}\n- FAILED: missing-post-button.\n`);
      await browser.close();
      process.exit(1);
    }
    await sleep(10000);

    await page.goto('https://www.linkedin.com/in/rick-johnson-584b593b8/recent-activity/all/', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(3500);
    const body = await page.locator('body').innerText().catch(() => '');
    const verified = body.includes('less volume and more truth') || body.includes('What metric in your business needs less volume and more truth?') || body.includes('This morning’s build-in-public update is simple');
    if (!verified) {
      await appendLog(`## ${new Date().toISOString()}\n- FAILED: post not verified in recent activity after publish click.\n`);
      await browser.close();
      process.exit(1);
    }

    await appendLog(`## ${new Date().toISOString()} - Daily Build-in-Public Post\n\n**Status:** POSTED LIVE\n**Profile:** https://www.linkedin.com/in/rick-johnson-584b593b8/\n**Posted via:** Playwright CDP (Chrome port 9225)\n**Source:** ${SOURCE}\n**Angle:** Revenue loop tightening: 1 charge, $7.99 last 24h, 246 active General contacts, 13.0% bounce+suppress, staged warm re-engagement.\n\n**Post text:**\n${POST_TEXT.split('\\n').map((line) => line ? '> ' + line : '> ').join('\\n')}\n\n---\n`);

    await browser.close();
  } catch (e) {
    try { await appendLog(`## ${new Date().toISOString()}\n- ERROR: ${e.message}\n`); } catch {}
    console.error(e);
    process.exit(1);
  }
}

main();
