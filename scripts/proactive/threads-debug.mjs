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

  // Try to find compose button
  const composeSelectors = [
    'a[href="/create"]',
    '[aria-label*="Create"]',
    '[aria-label*="New thread"]',
    '[aria-label*="New post"]',
    '[aria-label*="Compose"]',
  ];
  let found = false;
  for (const sel of composeSelectors) {
    const el = await page.$(sel);
    if (el) {
      console.log('Found compose btn:', sel);
      await el.evaluate(e => e.dispatchEvent(new MouseEvent('click', { bubbles: true })));
      found = true;
      await sleep(2500);
      break;
    }
  }
  if (!found) {
    console.log('No compose button — dumping aria-labels:');
    const labels = await page.evaluate(() => {
      const r = [];
      document.querySelectorAll('[aria-label]').forEach(el => r.push(el.tagName + ': ' + el.getAttribute('aria-label')));
      return r.slice(0, 30);
    });
    console.log(JSON.stringify(labels, null, 2));
  }

  // Try to find editor
  const editorSelectors = [
    '[contenteditable="true"]',
    '[role="textbox"]',
    'p[data-placeholder]',
    'div[data-placeholder]',
    '[data-lexical-editor]',
    'textarea',
  ];

  console.log('--- Checking editor selectors after compose click ---');
  for (const sel of editorSelectors) {
    const el = await page.$(sel);
    if (el) {
      const info = await el.evaluate(e => e.tagName + ' | class: ' + e.className.substring(0, 80) + ' | placeholder: ' + (e.getAttribute('placeholder') || e.getAttribute('data-placeholder') || ''));
      console.log('FOUND:', sel, '->', info);
    } else {
      console.log('MISS:', sel);
    }
  }

  await browser.close();
})().catch(e => { console.error('ERROR:', e.message); process.exit(1); });
