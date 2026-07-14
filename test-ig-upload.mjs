import { chromium } from 'playwright';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const ctx = browser.contexts()[0];
  const page = ctx.pages().find(p => p.url().includes('instagram.com')) || ctx.pages()[0];
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
  await sleep(1500);
  console.log('has input?', await page.evaluate(() => !!document.querySelector('input[type="file"]')));
  await page.setInputFiles('input[type="file"]', '/Users/rickthebot/.openclaw/workspace/dummy-video-2kb.mp4');
  console.log('setInputFiles ok');
  await sleep(5000);
  await page.screenshot({ path: '/Users/rickthebot/.openclaw/workspace/test-ig-upload.png', fullPage: false });
  console.log('url', page.url);
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });