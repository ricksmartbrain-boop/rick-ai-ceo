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
 await p.screenshot({path:'/Users/rickthebot/.openclaw/workspace/li-after-click.png', fullPage:true});
 console.log('saved');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
