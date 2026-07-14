const { chromium } = require('playwright');
(async() => {
 const browser = await chromium.connectOverCDP('http://127.0.0.1:9225');
 const context = browser.contexts()[0];
 const page = context.pages()[0];
 await page.goto('https://www.linkedin.com/feed/', {waitUntil:'domcontentloaded', timeout:30000});
 await page.waitForTimeout(3000);
 const btn = page.locator('button').filter({ hasText: 'Start a post' }).first();
 console.log('btn count', await btn.count());
 await btn.click({timeout:10000});
 await page.waitForTimeout(3000);
 const dialogs = await page.locator('[role="dialog"]').evaluateAll(els => els.map(e => ({hidden:e.getAttribute('aria-hidden'), label:e.getAttribute('aria-label'), text:(e.innerText||'').slice(0,200)})));
 console.log(JSON.stringify(dialogs, null, 2));
 await browser.close();
})().catch(err=>{console.error(err); process.exit(1);});
