#!/usr/bin/env node
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');

const PORT = 9225;
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
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

async function main() {
  const version = await waitForVersion();
  const browser = await chromium.connectOverCDP(version.webSocketDebuggerUrl || `http://127.0.0.1:${PORT}`);
  const ctx = browser.contexts()[0] || await browser.newContext();
  const page = ctx.pages()[0] || await ctx.newPage();
  await page.goto('https://www.linkedin.com/feed/?shareActive=true', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(3500);
  const editor = page.getByRole('textbox', { name: 'Text editor for creating content' });
  await editor.click({ timeout: 15000 });
  await page.keyboard.insertText('TEST-RICK-POST');
  await sleep(500);
  const composer = page.locator('div[role="dialog"]').filter({ has: editor }).first();
  const postBtn = composer.getByRole('button', { name: 'Post' }).first();
  const state = await postBtn.evaluate((el) => ({
    text: (el.innerText || el.textContent || '').trim(),
    aria: el.getAttribute('aria-label'),
    disabled: el.disabled,
    className: el.className,
    outer: el.outerHTML.slice(0, 500)
  }));
  const editorText = await editor.innerText().catch(() => '');
  console.log(JSON.stringify({ state, editorText }, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
