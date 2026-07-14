const { chromium } = require('playwright');
(async()=>{
 const browser = await chromium.connectOverCDP('http://127.0.0.1:9225');
 const page = browser.contexts()[0].pages()[0];
 await page.goto('https://www.linkedin.com/feed/', {waitUntil:'domcontentloaded', timeout:30000});
 await page.waitForTimeout(3000);
 const data = await page.evaluate(() => {
   const el = [...document.querySelectorAll('*')].find(e => (e.innerText||'').trim() === 'Start a post');
   if (!el) return null;
   let p = el, chain=[];
   for (let i=0; p && i<4; i++, p=p.parentElement) chain.push({tag:p.tagName, role:p.getAttribute('role'), cls:p.className, txt:(p.innerText||'').trim().slice(0,100)});
   return chain;
 });
 console.log(JSON.stringify(data, null, 2));
 await browser.close();
})().catch(err=>{console.error(err); process.exit(1);});
