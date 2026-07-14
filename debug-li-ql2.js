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
 await editor.click({timeout:10000});
 await p.keyboard.insertText('hello world');
 await p.waitForTimeout(1000);
 const state = await p.evaluate(() => {
   const ed = document.querySelector('div.ql-editor');
   const dialogs = [...document.querySelectorAll('[role="dialog"]')].map(d => ({text:(d.innerText||'').trim().slice(0,200), cls:d.className, visible: !!(d.offsetWidth || d.offsetHeight || d.getClientRects().length)}));
   const postBtn = [...document.querySelectorAll('button, [role="button"]')].find(el => (el.textContent||'').trim()==='Post');
   return {edText: ed ? ed.innerText : null, edHtml: ed ? ed.innerHTML : null, post: postBtn ? {text:(postBtn.textContent||'').trim(), disabled: postBtn.disabled || postBtn.getAttribute('aria-disabled')} : null, dialogs};
 });
 console.log(JSON.stringify(state,null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
