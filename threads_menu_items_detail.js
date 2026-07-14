const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/@meet_rick_ai/post/DXR5TbaCnkz', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(5000);
  await page.locator('div[role="button"]').nth(5).click({force:true});
  await page.waitForTimeout(1500);
  const items = await page.evaluate(() => {
    const menu = document.querySelector('[role="menu"]');
    if (!menu) return [];
    return Array.from(menu.querySelectorAll('*')).map((e,i)=>{
      const t=(e.textContent||'').trim();
      const r=e.getBoundingClientRect();
      return {i,tag:e.tagName,role:e.getAttribute('role'),text:t.slice(0,80),x:r.x,y:r.y,w:r.width,h:r.height,cls:e.className};
    }).filter(x=>x.text);
  });
  console.log(JSON.stringify(items,null,2));
  await browser.close();
})();
