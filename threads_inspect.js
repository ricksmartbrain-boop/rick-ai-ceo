const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const context = browser.contexts()[0];
  let page = context.pages()[0];
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(5000);
  console.log('URL', page.url());
  const data = await page.locator('textarea, input, [contenteditable="true"], button, [role="textbox"], [role="button"]').evaluateAll(els => els.slice(0,100).map((e,i)=>({i,tag:e.tagName,role:e.getAttribute('role'),type:e.getAttribute('type'),aria:e.getAttribute('aria-label'),placeholder:e.getAttribute('placeholder'),text:(e.innerText||e.value||'').slice(0,120),contenteditable:e.getAttribute('contenteditable')})));
  console.log(JSON.stringify(data,null,2));
  await browser.close();
})();
