import { chromium } from 'playwright';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const page = browser.contexts()[0]?.pages().find(p => p.url().includes('instagram.com')) || browser.contexts()[0]?.pages()[0];
  await page.goto('https://www.instagram.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(2000);
  await page.evaluate(() => {
    const els = [...document.querySelectorAll('a,button,div[role="button"]')];
    const found = els.find(el => (el.textContent || '').trim().toLowerCase().includes('create') || (el.getAttribute('aria-label') || '').toLowerCase().includes('create'));
    if (found) (found.closest('a,button,div[role="button"]') || found).click();
  });
  await sleep(1000);
  await page.evaluate(() => {
    const els = [...document.querySelectorAll('a,button,div[role="button"],div[role="menuitem"]')];
    const found = els.find(el => ((el.innerText || el.textContent || '').trim().toLowerCase().includes('post')) || ((el.getAttribute('aria-label') || '').toLowerCase().includes('post')));
    if (found) (found.closest('a,button,div[role="button"],div[role="menuitem"]') || found).click();
  });
  for (let i = 0; i < 10; i++) {
    const info = await page.evaluate(() => ({
      url: location.href,
      hasInput: !!document.querySelector('input[type="file"]'),
      hasDialog: !!document.querySelector('[role="dialog"], [aria-modal="true"]'),
      text: (document.body.innerText || '').slice(0, 160).replace(/\n+/g, ' | ')
    }));
    console.log(i, info);
    await sleep(1000);
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });