const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(3000);
  const els = page.locator('div[role="button"]');
  for (const idx of [4,5,36]) {
    const html = await els.nth(idx).evaluate(el=>el.outerHTML).catch(e=>'ERR:'+e.message);
    console.log('IDX', idx, html.slice(0,1000));
  }
  await browser.close();
})();
