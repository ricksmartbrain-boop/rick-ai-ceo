import { chromium } from 'playwright';
import fs from 'fs';
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const cookies = JSON.parse(fs.readFileSync('/Users/rickthebot/.config/instagram/cookies.json', 'utf8'));
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  await ctx.addCookies(cookies.map(c => c.domain?.includes('.instagram.com') ? { ...c, domain: '.threads.net' } : c));
  const page = await ctx.newPage();
  await page.goto('https://www.threads.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(4000);
  console.log('url1', page.url);
  console.log('login1', await page.evaluate(() => !!document.querySelector('input[name="username"], form[action*="login"]')));
  await page.evaluate(() => {
    const selectors = ['[aria-label="Create"]','[aria-label="New thread"]','a[href="/create"]','svg[aria-label="Create"]'];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) { (el.closest('a,button,[role="button"]') || el).click(); return; }
    }
    const btns = [...document.querySelectorAll('a,button,[role="button"]')];
    const create = btns.find(b => {
      const t = (b.textContent || '').trim().toLowerCase();
      const label = (b.getAttribute('aria-label') || '').toLowerCase();
      return t === 'create' || t === 'new thread' || label.includes('create') || label.includes('new thread');
    });
    if (create) create.click();
  });
  for (let i = 0; i < 6; i++) {
    const snapshot = await page.evaluate(() => ({
      url: location.href,
      body: (document.body.innerText || '').slice(0, 500).replace(/\n+/g, ' | '),
      dialogs: [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')].length,
      textboxes: [...document.querySelectorAll('[role="textbox"], [contenteditable="true"], textarea')].map(x => ({role:x.getAttribute('role'), ce:x.getAttribute('contenteditable'), text:(x.innerText||x.textContent||'').slice(0,80), ph:x.getAttribute('aria-label') || x.getAttribute('placeholder') || ''})),
    }));
    console.log('tick', i, JSON.stringify(snapshot));
    await sleep(1000);
  }
  await page.screenshot({ path: '/Users/rickthebot/.openclaw/workspace/inspect-threads.png', fullPage: false });
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });