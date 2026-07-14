const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(3000);
  await page.getByText("What's new?").click({timeout:10000}).catch(e=>console.error('click failed', e.message));
  await page.waitForTimeout(1000);
  await page.keyboard.type('hello', {delay:50}).catch(e=>console.error('type failed', e.message));
  await page.waitForTimeout(1000);
  console.log('URL', page.url());
  const data = await page.locator('textarea, input, [contenteditable="true"], button, [role="textbox"], [role="button"]').evaluateAll(els => els.slice(0,20).map((e,i)=>({i,tag:e.tagName,role:e.getAttribute('role'),aria:e.getAttribute('aria-label'),text:(e.innerText||e.value||'').slice(0,200),placeholder:e.getAttribute('placeholder')})));
  console.log(JSON.stringify(data,null,2));
  await browser.close();
})();
