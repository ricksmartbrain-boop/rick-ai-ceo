const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/@meet_rick_ai', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForTimeout(5000);

  // Click directly on the test post to open it
  await page.getByText('Testing post format').first().click({timeout:10000});
  await page.waitForTimeout(3000);
  console.log('URL after clicking post:', page.url());

  // Look for more/delete options in the post view
  const btns = await page.locator('div[role="button"], button').evaluateAll(els => els.slice(0,30).map(e=>({aria:e.getAttribute('aria-label'), text:(e.innerText||'').trim().slice(0,50)})));
  console.log('Buttons on post page:', JSON.stringify(btns.filter(b=>b.aria||b.text)));
  await browser.close();
})();
