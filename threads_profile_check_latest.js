const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.goto('https://www.threads.com/@meet_rick_ai', {waitUntil:'networkidle', timeout:40000}).catch(()=>{});
  await page.waitForTimeout(6000);
  const text = await page.textContent('body').catch(()=> '');
  const links = await page.locator('a[href*="/@meet_rick_ai/post/"]').evaluateAll(els => [...new Set(els.map(e => e.href))]);
  console.log(JSON.stringify({
    has2384: text.includes('2,384'),
    hasNarrating: text.includes('narrating the company'),
    hasCashIn: text.includes('Cash-in and recurring revenue'),
    hasTodayLesson: text.includes("Today's real lesson"),
    hasTesting: text.includes('Testing post format'),
    links,
    snippet: text.slice(0, 5000)
  }, null, 2));
  await browser.close();
})();
