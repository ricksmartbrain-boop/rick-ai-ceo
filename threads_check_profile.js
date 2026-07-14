const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.goto('https://www.threads.com/@meetrickai', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(6000);
  const text = await page.textContent('body').catch(()=> '');
  const needles = ['cash-in from recurring revenue', '2,384', 'MRR', 'meetrick.ai'];
  console.log({url: page.url(), title: await page.title().catch(()=>''), found: Object.fromEntries(needles.map(n=>[n, text.includes(n)])), snippet: text.slice(0,1000)});
  await browser.close();
})();
