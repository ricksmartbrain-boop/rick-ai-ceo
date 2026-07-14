const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9225');
 const ctx=browser.contexts()[0];
 const p=ctx.pages()[0];
 await p.goto('https://www.linkedin.com/feed/',{waitUntil:'domcontentloaded',timeout:30000});
 await p.waitForTimeout(4000);
 console.log('before', ctx.pages().map(pg=>pg.url()));
 await p.locator('[role="button"]').filter({ hasText: 'Start a post' }).first().click({force:true, timeout:10000});
 await p.waitForTimeout(3000);
 console.log('after', ctx.pages().map(pg=>pg.url()));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
