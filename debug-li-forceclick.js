const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9225');
 const p=browser.contexts()[0].pages()[0];
 await p.goto('https://www.linkedin.com/feed/',{waitUntil:'domcontentloaded',timeout:30000});
 await p.waitForTimeout(4000);
 const a = p.locator('[aria-label="Start a post"]').first();
 console.log('aria count', await a.count());
 await a.click({timeout:10000, force:true});
 await p.waitForTimeout(3000);
 const info = await p.evaluate(() => [...document.querySelectorAll('[contenteditable="true"], [role="textbox"], [role="dialog"], textarea')].map(el => ({tag:el.tagName, role:el.getAttribute('role'), aria:el.getAttribute('aria-label'), placeholder:el.getAttribute('placeholder'), text:(el.innerText||el.textContent||'').trim().slice(0,200), cls:el.className})).slice(0,30));
 console.log(JSON.stringify(info,null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
