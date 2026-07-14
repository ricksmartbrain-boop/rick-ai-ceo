const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9225');
 const p=browser.contexts()[0].pages()[0];
 await p.goto('https://www.linkedin.com/feed/',{waitUntil:'domcontentloaded',timeout:30000});
 await p.waitForTimeout(4000);
 const loc = p.locator('[role="button"]').filter({ hasText: 'Start a post' }).first();
 const box = await loc.boundingBox();
 if (box) await p.mouse.click(box.x + box.width/2, box.y + box.height/2);
 await p.waitForTimeout(2500);
 const data = {
   textboxCount: await p.getByRole('textbox').count().catch(()=>-1),
   contenteditableCount: await p.locator('[contenteditable="true"]').count().catch(()=>-1),
   dialogCount: await p.locator('[role="dialog"]').count().catch(()=>-1),
   textQuestionCount: await p.getByText('What do you want to talk about?').count().catch(()=>-1),
   postButtonCount: await p.getByRole('button', { name: /Post/ }).count().catch(()=>-1),
   allButtons: await p.locator('button, [role="button"]').evaluateAll(els => els.map(el => ({t:(el.textContent||'').trim().slice(0,100), role:el.getAttribute('role'), aria:el.getAttribute('aria-label')||'', cls:el.className})).slice(0,50)).catch(()=>[])
 };
 console.log(JSON.stringify(data,null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
