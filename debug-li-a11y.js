const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9225');
 const p=browser.contexts()[0].pages()[0];
 await p.goto('https://www.linkedin.com/feed/',{waitUntil:'domcontentloaded',timeout:30000});
 await p.waitForTimeout(4000);
 await p.locator('[aria-label="Start a post"]').first().click({force:true, timeout:10000});
 await p.waitForTimeout(3000);
 const snap = await p.accessibility.snapshot({interestingOnly:true});
 function prune(node, depth=0){
   if(!node || depth>4) return null;
   const out={role:node.role, name:node.name, value:node.value, children:[]};
   if(node.children) out.children=node.children.map(c=>prune(c,depth+1)).filter(Boolean).slice(0,20);
   return out;
 }
 console.log(JSON.stringify(prune(snap), null, 2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
