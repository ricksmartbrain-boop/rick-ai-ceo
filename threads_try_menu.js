const { chromium } = require('playwright');
(async()=>{
  const which = Number(process.argv[2] || '4');
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/@meet_rick_ai/post/DXR5TbaCnkz', {waitUntil:'networkidle', timeout:40000}).catch(()=>{});
  await page.waitForTimeout(4000);
  const btn = page.locator('div[role="button"]').nth(which);
  const box = await btn.boundingBox();
  console.log('clicking index', which, 'box', box);
  await btn.click({force:true}).catch(e=>console.log('click err', e.message));
  await page.waitForTimeout(2000);
  const text = await page.textContent('body').catch(()=> '');
  console.log(text.slice(0,2500));
  await browser.close();
})();
