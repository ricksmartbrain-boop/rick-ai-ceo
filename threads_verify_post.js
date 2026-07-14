const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(5000);
  const text = await page.textContent('body').catch(()=> '');
  const needles = ['2,384', 'cash-in from recurring revenue', 'meetrick.ai', 'MRR'];
  console.log({url: page.url(), title: await page.title().catch(()=>''), found: Object.fromEntries(needles.map(n=>[n, text.includes(n)]))});
  await browser.close();
})();
