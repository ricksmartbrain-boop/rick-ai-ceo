import { chromium } from 'playwright';

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const contexts = browser.contexts();
  let page = contexts[0]?.pages().find(p => p.url().includes('threads'));
  if (!page) {
    page = await contexts[0].newPage();
  }

  await page.goto('https://www.threads.com/', { waitUntil: 'domcontentloaded', timeout: 25000 });
  await sleep(3000);

  // Click compose
  const btn = await page.$('[aria-label*="Create"]');
  if (btn) {
    await btn.evaluate(e => e.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    console.log('Clicked Create');
    await sleep(4000);
  }

  // Navigate to create URL instead
  console.log('Navigating to /create directly...');
  await page.goto('https://www.threads.com/create', { waitUntil: 'domcontentloaded', timeout: 25000 });
  await sleep(4000);
  console.log('URL:', page.url());

  // Deep DOM dump
  const allInteractive = await page.evaluate(() => {
    const r = [];
    const els = document.querySelectorAll('[contenteditable], [role="textbox"], textarea, input[type="text"]');
    els.forEach(el => r.push({
      tag: el.tagName,
      ce: el.getAttribute('contenteditable'),
      role: el.getAttribute('role'),
      type: el.getAttribute('type'),
      ph: el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || el.getAttribute('aria-placeholder') || '',
      cl: el.className.substring(0, 60),
    }));
    return r;
  });
  console.log('Interactive elements:', JSON.stringify(allInteractive, null, 2));

  // Dump visible text on page to understand what's rendered
  const bodyText = await page.evaluate(() => document.body.innerText.substring(0, 500));
  console.log('Body text:', bodyText);

  await browser.close();
})().catch(e => { console.error('ERROR:', e.message); process.exit(1); });
