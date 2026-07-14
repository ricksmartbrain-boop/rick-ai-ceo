const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/@meet_rick_ai/post/DXR5TbaCnkz', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(5000);
  await page.locator('div[role="button"]').nth(5).click({force:true});
  await page.waitForTimeout(1500);
  const res = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('*'));
    return all.filter(e => (e.textContent||'').trim() === 'Delete' || (e.textContent||'').includes('Delete')).slice(0,20).map(e=>{
      const r = e.getBoundingClientRect();
      return {tag:e.tagName, text:(e.textContent||'').trim().slice(0,200), cls:e.className, role:e.getAttribute('role'), x:r.x,y:r.y,w:r.width,h:r.height};
    });
  });
  console.log(JSON.stringify(res,null,2));
  await browser.close();
})();
