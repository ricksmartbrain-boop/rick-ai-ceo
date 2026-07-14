const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/@meet_rick_ai/post/DXR5TbaCnkz', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(5000);
  await page.locator('div[role="button"]').nth(5).click({force:true});
  await page.waitForTimeout(1000);
  await page.locator('[role="menuitem"]').filter({ hasText: /^Delete$/ }).first().click({force:true}).catch(async e=>{
    console.log('menuitem click err', e.message);
    const box = await page.locator('[role="menuitem"]').filter({ hasText: /^Delete$/ }).first().boundingBox();
    console.log('box', box);
    if (box) await page.mouse.click(box.x+box.width/2, box.y+box.height/2);
  });
  await page.waitForTimeout(1500);
  const body = await page.textContent('body').catch(()=> '');
  console.log(body.slice(0,3000));
  const dels = await page.evaluate(() => Array.from(document.querySelectorAll('*')).filter(e => (e.textContent||'').trim()==='Delete').map(e=>({tag:e.tagName, role:e.getAttribute('role'), text:e.textContent.trim(), cls:e.className, rect:e.getBoundingClientRect().toJSON?.()||{}})).slice(0,20));
  console.log(JSON.stringify(dels,null,2));
  await browser.close();
})();
