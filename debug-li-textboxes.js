const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9225');
 const p=browser.contexts()[0].pages()[0];
 await p.goto('https://www.linkedin.com/feed/',{waitUntil:'domcontentloaded',timeout:30000});
 await p.waitForTimeout(4000);
 const loc = p.locator('[role="button"]').filter({ hasText: 'Start a post' }).first();
 const box = await loc.boundingBox();
 if (box) await p.mouse.click(box.x + box.width/2, box.y + box.height/2);
 await p.waitForTimeout(3000);
 const infos = await p.locator('[contenteditable="true"], [role="textbox"]').evaluateAll(els => els.map((el,i)=>({i, tag:el.tagName, role:el.getAttribute('role'), aria:el.getAttribute('aria-label')||'', placeholder:el.getAttribute('placeholder')||'', text:(el.innerText||el.textContent||'').trim().slice(0,120), cls:el.className, visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)})));
 console.log(JSON.stringify(infos,null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
