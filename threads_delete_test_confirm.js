const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/@meet_rick_ai/post/DXR5TbaCnkz', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(5000);

  await page.locator('div[role="button"]').nth(5).click({force:true});
  await page.waitForTimeout(800);
  await page.locator('[role="menuitem"]').filter({ hasText: /^Delete$/ }).first().click({force:true});
  await page.waitForTimeout(800);

  const confirm = page.locator('div[role="button"]').filter({ hasText: /^Delete$/ }).first();
  await confirm.waitFor({state:'visible', timeout:10000});
  await confirm.click({force:true});
  await page.waitForTimeout(4000);

  await page.goto('https://www.threads.com/@meet_rick_ai', {waitUntil:'networkidle', timeout:40000}).catch(()=>{});
  await page.waitForTimeout(4000);
  const text = await page.textContent('body').catch(()=> '');
  const gone = !text.includes('Testing post format');
  console.log('TEST_POST_GONE:', gone);
  console.log(text.slice(0,2500));
  await browser.close();
  process.exit(gone ? 0 : 1);
})();
