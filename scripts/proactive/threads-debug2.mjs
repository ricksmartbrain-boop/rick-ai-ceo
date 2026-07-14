import { chromium } from 'playwright';

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const contexts = browser.contexts();
  let page = contexts[0]?.pages().find(p => p.url().includes('threads.net'));
  if (!page) {
    page = await contexts[0].newPage();
  }

  await page.goto('https://www.threads.net/', { waitUntil: 'domcontentloaded', timeout: 25000 });
  await sleep(3000);

  // Click compose
  const btn = await page.$('[aria-label*="Create"]');
  if (btn) {
    await btn.evaluate(e => e.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    console.log('Clicked Create');
    await sleep(3500); // wait a bit longer for modal
  }

  // Dump all aria-labels after modal opens
  console.log('--- Post-click aria-labels ---');
  const labels = await page.evaluate(() => {
    const r = [];
    document.querySelectorAll('[aria-label]').forEach(el => r.push(el.tagName + ': ' + el.getAttribute('aria-label').substring(0, 80)));
    return r.slice(0, 40);
  });
  console.log(JSON.stringify(labels, null, 2));

  // Check for dialog / modal
  console.log('\n--- Dialog / modal detection ---');
  const dialog = await page.$('[role="dialog"], [aria-modal="true"]');
  if (dialog) {
    console.log('Found dialog');
    const inner = await dialog.evaluate(el => el.innerHTML.substring(0, 500));
    console.log('Dialog inner:', inner);
  } else {
    console.log('No dialog found');
  }

  // Check URL
  console.log('Current URL:', page.url());

  await browser.close();
})().catch(e => { console.error('ERROR:', e.message); process.exit(1); });
