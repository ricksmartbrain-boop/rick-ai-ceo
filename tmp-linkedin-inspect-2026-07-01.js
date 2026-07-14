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
  await page.goto('https://www.linkedin.com/in/rick-johnson-584b593b8/recent-activity/all/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(3500);
  const body = await page.locator('body').innerText().catch(() => '');
  console.log(body.slice(0, 20000));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
