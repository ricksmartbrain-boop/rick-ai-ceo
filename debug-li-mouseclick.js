const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9225');
 const ctx=browser.contexts()[0];
 const p=ctx.pages()[0];
 await p.goto('https://www.linkedin.com/feed/',{waitUntil:'domcontentloaded',timeout:30000});
 await p.waitForTimeout(4000);
 const loc = p.locator('[role="button"]').filter({ hasText: 'Start a post' }).first();
 const box = await loc.boundingBox();
 console.log('box', box);
 if (box) await p.mouse.click(box.x + box.width/2, box.y + box.height/2);
 await p.waitForTimeout(3000);
 const info = await p.evaluate(() => [...document.querySelectorAll('[contenteditable="true"], [role="textbox"], [role="dialog"], textarea')].map(el => ({tag:el.tagName, role:el.getAttribute('role'), aria:el.getAttribute('aria-label'), placeholder:el.getAttribute('placeholder'), text:(el.innerText||el.textContent||'').trim().slice(0,200), cls:el.className})).slice(0,30));
 console.log(JSON.stringify(info,null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
