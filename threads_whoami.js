const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  // Check current logged-in user via profile link
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(4000);
  // Find profile links
  const links = await page.locator('a[href*="/@"]').evaluateAll(els => els.slice(0,20).map(e=>({href:e.href, text:(e.innerText||'').slice(0,80)})));
  console.log('Profile links:', JSON.stringify(links,null,2));
  // Also check nav items
  const navText = await page.locator('nav, [aria-label*="nav" i], [role="navigation"]').evaluateAll(els=>els.map(e=>(e.innerText||'').slice(0,200)));
  console.log('Nav:', JSON.stringify(navText,null,2));
  await browser.close();
})();
