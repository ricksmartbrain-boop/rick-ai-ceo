const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9225');
 const p=browser.contexts()[0].pages()[0];
 await p.goto('https://www.linkedin.com/feed/',{waitUntil:'domcontentloaded',timeout:30000});
 await p.waitForTimeout(4000);
 console.log('url', p.url());
 const data = await p.evaluate(() => {
   const out = {buttons:[], roleButtons:[], ariaButtons:[]};
   for (const el of document.querySelectorAll('button')) {
     const t=(el.textContent||'').trim();
     const aria=el.getAttribute('aria-label')||'';
     if (t.toLowerCase().includes('start a post') || aria.toLowerCase().includes('start a post')) out.buttons.push({t, aria, cls: el.className});
   }
   for (const el of document.querySelectorAll('[role="button"]')) {
     const t=(el.textContent||'').trim();
     const aria=el.getAttribute('aria-label')||'';
     if (t.toLowerCase().includes('start a post') || aria.toLowerCase().includes('start a post')) out.roleButtons.push({t, aria, cls: el.className});
   }
   for (const el of document.querySelectorAll('[aria-label]')) {
     const aria=(el.getAttribute('aria-label')||'').trim();
     const t=(el.textContent||'').trim();
     if (aria.toLowerCase().includes('start a post')) out.ariaButtons.push({tag:el.tagName, role:el.getAttribute('role'), aria, t:t.slice(0,120), cls:el.className});
   }
   return out;
 });
 console.log(JSON.stringify(data,null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
