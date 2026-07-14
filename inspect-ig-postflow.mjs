import { chromium } from 'playwright';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const page = browser.contexts()[0]?.pages().find(p => p.url().includes('instagram.com')) || browser.contexts()[0]?.pages()[0];
  await page.goto('https://www.instagram.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(2000);
  await page.evaluate(() => {
    const els = [...document.querySelectorAll('a,button,div[role="button"]')];
    const found = els.find(el => {
      const text = (el.textContent || '').trim().toLowerCase();
      const label = (el.getAttribute('aria-label') || '').toLowerCase();
      return text.includes('create') || text.includes('new post') || label.includes('create') || label.includes('new post');
    });
    if (found) (found.closest('a,button,div[role="button"]') || found).click();
  });
  await sleep(1200);
  const before = await page.evaluate(() => ({
    url: location.href,
    body: (document.body.innerText || '').slice(0, 800),
    inputs: [...document.querySelectorAll('input')].map(i => ({type: i.type, name: i.name, accept: i.accept, visible: !!(i.offsetWidth || i.offsetHeight || i.getClientRects().length)})).slice(0, 20)
  }));
  console.log('BEFORE_POST', JSON.stringify(before, null, 2));
  await page.evaluate(() => {
    const els = [...document.querySelectorAll('a,button,div[role="button"],div[role="menuitem"]')];
    const found = els.find(el => {
      const text = ((el.innerText || el.textContent || '').trim()).toLowerCase();
      const label = (el.getAttribute('aria-label') || '').toLowerCase();
      return text.includes('post') || label.includes('post');
    });
    if (found) (found.closest('a,button,div[role="button"],div[role="menuitem"]') || found).click();
  });
  await sleep(1500);
  const after = await page.evaluate(() => ({
    url: location.href,
    body: (document.body.innerText || '').slice(0, 1200),
    inputs: [...document.querySelectorAll('input')].map(i => ({type: i.type, name: i.name, accept: i.accept, visible: !!(i.offsetWidth || i.offsetHeight || i.getClientRects().length), value: i.value})).slice(0, 20),
    dialogs: [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')].length
  }));
  console.log('AFTER_POST', JSON.stringify(after, null, 2));
  await page.screenshot({ path: '/Users/rickthebot/.openclaw/workspace/inspect-ig-postflow.png', fullPage: false });
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });