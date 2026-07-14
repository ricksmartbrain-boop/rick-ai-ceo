import { chromium } from 'playwright';
const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
const ctx = browser.contexts()[0] || await browser.newContext();
const page = ctx.pages()[0] || await ctx.newPage();
await page.goto('https://www.instagram.com/', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
await page.waitForTimeout(5000);
console.log('URL', page.url());
console.log('TITLE', await page.title().catch(()=>''));
const body = await page.locator('body').innerText({timeout:5000}).catch(()=> '');
console.log('BODY', body.slice(0,1500));
for (const sel of ['article','button[aria-label="Like"]','div[role="button"][aria-label="Like"]','button:has-text("Like")','svg[aria-label="Like"]']) {
  console.log(sel, await page.locator(sel).count().catch(()=>-1));
}
await browser.close();
