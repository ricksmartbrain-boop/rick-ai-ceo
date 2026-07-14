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
 const editor = p.locator('div.ql-editor').first();
 const box2 = await editor.boundingBox();
 if (box2) {
   await p.mouse.click(box2.x + 20, box2.y + 20);
   await p.keyboard.type('hello world', {delay:10});
 }
 await p.waitForTimeout(1200);
 const r = p.getByRole('button', {name:'Post'});
 const c = await r.count();
 const arr = [];
 for (let i=0;i<c;i++) {
   const l = r.nth(i);
   arr.push({i, text: await l.textContent().catch(()=>null), box: await l.boundingBox().catch(()=>null), vis: await l.isVisible().catch(()=>false), disabled: await l.isDisabled().catch(()=>null)});
 }
 console.log(JSON.stringify(arr,null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
