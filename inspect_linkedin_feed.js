const { chromium } = require('playwright');
(async() => {
 const browser = await chromium.connectOverCDP('http://127.0.0.1:9225');
 const context = browser.contexts()[0];
 const page = context.pages()[0];
 await page.goto('https://www.linkedin.com/feed/', {waitUntil:'domcontentloaded', timeout:30000});
 await page.waitForTimeout(3000);
 const texts = await page.evaluate(() => {
   return [...document.querySelectorAll('button, div[role="button"], a')].map(el => ({txt: (el.innerText||el.textContent||'').trim(), aria: el.getAttribute('aria-label')||'', cls: el.className||''})).filter(x => x.txt.includes('Start a post') || x.aria.includes('Start a post')).slice(0,20);
 });
 console.log(JSON.stringify(texts, null, 2));
 await browser.close();
})().catch(err=>{console.error(err); process.exit(1);});
