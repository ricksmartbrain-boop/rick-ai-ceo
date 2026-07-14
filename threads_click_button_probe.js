const { chromium } = require('playwright');
(async()=>{
  const which = Number(process.argv[2] || '5');
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/@meet_rick_ai/post/DXR5TbaCnkz', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(5000);
  const btn = page.locator('div[role="button"]').nth(which);
  const box = await btn.boundingBox();
  console.log('clicking', which, JSON.stringify(box));
  await btn.click({force:true}).catch(e=>console.log('click err', e.message));
  await page.waitForTimeout(1500);
  const body = await page.textContent('body').catch(()=> '');
  const interesting = ['Delete','Remove','Edit','Copy link','Share','Cancel','Unfollow','Hide like count','Archive'];
  console.log(JSON.stringify(Object.fromEntries(interesting.map(k => [k, body.includes(k)])), null, 2));
  console.log('url', page.url());
  await browser.close();
})();
