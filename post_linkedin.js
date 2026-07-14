const { chromium } = require('playwright');

(async() => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9225');
  const context = browser.contexts()[0] || await browser.newContext();
  let page = context.pages()[0] || await context.newPage();
  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  const url = page.url();
  console.log('URL:', url);
  const title = await page.title().catch(()=> '');
  console.log('TITLE:', title);
  const bodyText = await page.locator('body').innerText({ timeout: 5000 }).catch(()=> '');
  console.log('BODY_HEAD:', bodyText.slice(0, 500).replace(/\n/g, ' | '));
  await browser.close();
})().catch(err => { console.error(err); process.exit(1); });
