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

  const texts = await page.locator('button').evaluateAll(buttons => buttons.map((b) => {
    const r = b.getBoundingClientRect();
    const s = getComputedStyle(b);
    return {
      text: (b.innerText || b.textContent || '').trim().replace(/\s+/g, ' '),
      aria: b.getAttribute('aria-label'),
      title: b.getAttribute('title'),
      visible: !!(r.width && r.height && r.bottom > 0 && r.right > 0 && s.visibility !== 'hidden' && s.display !== 'none')
    };
  }).filter(x => x.visible && (x.text || x.aria || x.title)));
  console.log(JSON.stringify(texts.slice(0, 80), null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
