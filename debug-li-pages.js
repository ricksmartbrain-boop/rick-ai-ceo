const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9225');
 const ctx=browser.contexts()[0];
 console.log(JSON.stringify(ctx.pages().map((p,i)=>({i,url:p.url()})), null, 2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
