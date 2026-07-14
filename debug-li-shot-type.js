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
   await p.keyboard.type('hello world', {delay: 10});
 }
 await p.waitForTimeout(1500);
 await p.screenshot({path:'/Users/rickthebot/.openclaw/workspace/li-typed.png', fullPage:true});
 console.log('done');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
