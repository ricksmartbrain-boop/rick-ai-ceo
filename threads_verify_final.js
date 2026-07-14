const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.goto('https://www.threads.com/@meet_rick_ai', {waitUntil:'networkidle', timeout:40000}).catch(()=>{});
  await page.waitForTimeout(6000);
  const profileText = await page.textContent('body').catch(()=>'');
  const posted = profileText.includes('2,384') || profileText.includes('cash-in') || profileText.includes('recurring revenue');
  const snippet = profileText.slice(200, 3000);
  console.log('Profile has new post?', posted);
  console.log('Snippet:', snippet);
  await browser.close();
})();
