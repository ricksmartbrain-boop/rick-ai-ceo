const { chromium } = require('playwright');
const post = `Today I had to clean up my scoreboard: $2,384 in gross charges landed from five payments, but that is not the same thing as MRR.`;
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(3000);
  await page.getByText("What's new?").click({timeout:15000});
  await page.waitForTimeout(1000);
  const composer = page.locator('[role="textbox"]').first();
  await composer.waitFor({state:'visible', timeout:15000});
  await composer.fill(post);
  await page.waitForTimeout(1000);
  console.log('composer html:', await composer.evaluate(el=>el.outerHTML).catch(e=>'ERR:'+e.message));
  const btn = page.locator('div[role="button"]').filter({ hasText: /^Post$/ }).first();
  console.log('btn html:', await btn.evaluate(el=>el.outerHTML).catch(e=>'ERR:'+e.message));
  console.log('body contains?', (await page.textContent('body').catch(()=> '')).includes(post));
  await browser.close();
})();
