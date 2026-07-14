const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9225');
 const p=browser.contexts()[0].pages()[0];
 await p.goto('https://www.linkedin.com/feed/',{waitUntil:'domcontentloaded',timeout:30000});
 await p.waitForTimeout(4000);
 const html = await p.evaluate(() => {
   const el = [...document.querySelectorAll('[role="button"]')].find(e => (e.textContent||'').trim()==='Start a post');
   if (!el) return null;
   return {outer: el.outerHTML.slice(0,2000), parent: el.parentElement?.outerHTML.slice(0,2000)};
 });
 console.log(JSON.stringify(html, null, 2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
