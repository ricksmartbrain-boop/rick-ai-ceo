const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9225');
 const p=browser.contexts()[0].pages()[0];
 await p.goto('https://www.linkedin.com/feed/',{waitUntil:'domcontentloaded',timeout:30000});
 await p.waitForTimeout(4000);
 const btn = p.locator('[role="button"]').filter({ hasText: 'Start a post' }).first();
 const box = await btn.boundingBox();
 if (box) await p.mouse.click(box.x+box.width/2, box.y+box.height/2);
 await p.waitForTimeout(3000);
 const composer = p.locator('[role="dialog"]').filter({ hasText: 'What do you want to talk about?' }).first();
 const editor = composer.locator('div.ql-editor').first();
 const box2 = await editor.boundingBox();
 if (box2) {
   await p.mouse.click(box2.x + 20, box2.y + 20);
   await p.keyboard.type('hello world', {delay:10});
 }
 await p.waitForTimeout(1200);
 const r = p.getByRole('button', {name:'Post'});
 const t = p.getByText('Post');
 console.log(JSON.stringify({roleCount: await r.count().catch(()=>-1), textCount: await t.count().catch(()=>-1), roleBox: await r.boundingBox().catch(()=>null), textBox: await t.boundingBox().catch(()=>null)}, null, 2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
