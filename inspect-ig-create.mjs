import { chromium } from 'playwright';

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const page = browser.contexts()[0]?.pages().find(p => p.url().includes('instagram.com')) || browser.contexts()[0]?.pages()[0];
  await page.goto('https://www.instagram.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(2000);
  const login = await page.evaluate(() => document.querySelector('a[href*="/accounts/login"]') ? 'logged_out' : 'logged_in');
  console.log('login=', login);
  await page.evaluate(() => {
    const els = [...document.querySelectorAll('a,button,div[role="button"],svg')];
    const found = els.find(el => {
      const text = (el.textContent || '').trim().toLowerCase();
      const label = (el.getAttribute?.('aria-label') || '').toLowerCase();
      return text.includes('create') || text.includes('new post') || label.includes('create') || label.includes('new post');
    });
    if (found) (found.closest('a,button,div[role="button"]') || found).click();
  });
  await sleep(1500);
  const dump = await page.evaluate(() => {
    const els = [...document.querySelectorAll('a,button,div[role="button"],div[role="menuitem"],li,[role="dialog"] *')];
    return els.slice(0, 80).map(el => ({
      tag: el.tagName,
      role: el.getAttribute('role'),
      label: el.getAttribute('aria-label'),
      text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80),
      cls: (el.className || '').toString().slice(0, 60),
    })).filter(x => x.text || x.label);
  });
  console.log(JSON.stringify(dump, null, 2));
  await page.screenshot({ path: '/Users/rickthebot/.openclaw/workspace/inspect-ig-create.png', fullPage: false });
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });