import { chromium } from 'playwright';

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const contexts = browser.contexts();
  let page = contexts[0]?.pages().find(p => p.url().includes('threads'));
  if (!page) {
    page = await contexts[0].newPage();
  }

  // Note: URL is https://www.threads.com/ (not threads.net)
  await page.goto('https://www.threads.com/', { waitUntil: 'domcontentloaded', timeout: 25000 });
  await sleep(3000);

  // Click compose
  const btn = await page.$('[aria-label*="Create"]');
  if (btn) {
    await btn.evaluate(e => e.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    console.log('Clicked Create');
    await sleep(3500);
  }

  // Find dialog
  const dialog = await page.$('[role="dialog"], [aria-modal="true"]');
  if (!dialog) { console.log('No dialog'); await browser.close(); return; }

  // Search for contenteditable inside dialog
  const editables = await dialog.$$('[contenteditable="true"], [role="textbox"], textarea, p[data-placeholder], div[data-placeholder]');
  console.log(`Found ${editables.length} editable elements in dialog`);

  for (const el of editables) {
    const info = await el.evaluate(e => ({
      tag: e.tagName,
      role: e.getAttribute('role'),
      ce: e.getAttribute('contenteditable'),
      placeholder: e.getAttribute('placeholder') || e.getAttribute('data-placeholder') || e.getAttribute('aria-label') || '',
      class: e.className.substring(0, 60),
    }));
    console.log('Editable:', JSON.stringify(info));
  }

  // Also try clicking into dialog to trigger lazy rendering
  console.log('\n--- Clicking dialog center to trigger render ---');
  await dialog.click().catch(() => {});
  await sleep(1500);

  const editables2 = await dialog.$$('[contenteditable="true"], [role="textbox"], textarea');
  console.log(`After click: Found ${editables2.length} editable elements`);

  // Dump all p tags in dialog
  const pTags = await dialog.$$('p');
  console.log(`Found ${pTags.length} <p> tags in dialog`);
  for (const p of pTags.slice(0, 5)) {
    const info = await p.evaluate(e => ({ ce: e.getAttribute('contenteditable'), placeholder: e.getAttribute('data-placeholder') || '' }));
    console.log('p:', JSON.stringify(info));
  }

  await browser.close();
})().catch(e => { console.error('ERROR:', e.message); process.exit(1); });
