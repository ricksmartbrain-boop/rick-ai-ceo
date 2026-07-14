const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/@meet_rick_ai/post/DXR5TbaCnkz', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(5000);
  await page.locator('div[role="button"]').nth(5).click({force:true});
  await page.waitForTimeout(1500);
  const items = await page.locator('div[role="button"], button').evaluateAll(els => els.map((e,i)=>({i,text:(e.innerText||'').trim().slice(0,80),aria:e.getAttribute('aria-label'),cls:e.className.slice(0,120)})).filter(x=>x.text||x.aria));
  console.log(JSON.stringify(items,null,2));
  await browser.close();
})();
