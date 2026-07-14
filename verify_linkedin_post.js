const { chromium } = require('playwright');
(async()=>{
 const browser = await chromium.connectOverCDP('http://127.0.0.1:9225');
 const page = browser.contexts()[0].pages()[0];
 await page.goto('https://www.linkedin.com/in/rick-johnson-584b593b8/recent-activity/all/', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
 await page.waitForTimeout(5000);
 const body = await page.locator('body').innerText().catch(()=> '');
 console.log(body.slice(0, 3000));
 await browser.close();
})().catch(err=>{console.error(err); process.exit(1);});
