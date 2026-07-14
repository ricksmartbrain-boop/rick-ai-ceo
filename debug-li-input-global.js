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
 console.log('editor box', box2);
 if (box2) {
   await p.mouse.click(box2.x + 10, box2.y + 10);
   await p.keyboard.type('hello world', {delay: 20});
 }
 await p.waitForTimeout(1000);
 const state = await p.evaluate(() => {
   const ed = [...document.querySelectorAll('*')].find(el => el.classList && el.classList.contains('ql-editor'));
   const post = [...document.querySelectorAll('button, [role="button"]')].find(el => (el.textContent||'').trim()==='Post');
   return {hasEditor: !!ed, edText: ed ? ed.innerText : null, post: post ? {disabled: post.disabled || post.getAttribute('aria-disabled'), text:(post.textContent||'').trim()} : null};
 });
 console.log(JSON.stringify(state,null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
