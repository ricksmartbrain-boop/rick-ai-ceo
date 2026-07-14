const { chromium } = require('playwright');
const POST_TEXT = `Test post from Rick — ignore.`;
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForTimeout(3000);
  const createBtn = page.locator('a[href="/create"], [aria-label*="Create" i]').first();
  await createBtn.click({timeout:10000});
  await page.waitForTimeout(2000);
  const composer = page.locator('[role="dialog"] [contenteditable="true"]').first();
  await composer.waitFor({state:'visible', timeout:15000});
  await composer.click();
  await page.keyboard.type('Test', {delay:50});
  await page.waitForTimeout(1000);

  // Inspect the Post button deeply
  const btnInfo = await page.locator('[role="dialog"] div[role="button"]').filter({ hasText: /^Post$/ }).first().evaluate(el => {
    const r = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return {
      outerHTML: el.outerHTML.slice(0,800),
      disabled: el.getAttribute('aria-disabled'),
      tabIndex: el.tabIndex,
      pointerEvents: style.pointerEvents,
      display: style.display,
      visibility: style.visibility,
      opacity: style.opacity,
      zIndex: style.zIndex,
      rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
      // Check what's directly in front at center
      elementAtCenter: (() => {
        const cx = r.x + r.width/2; const cy = r.y + r.height/2;
        const top = document.elementFromPoint(cx, cy);
        return top ? top.tagName + '|' + top.getAttribute('role') + '|cls:' + top.className.slice(0,100) : 'null';
      })()
    };
  });
  console.log(JSON.stringify(btnInfo, null, 2));

  // Also check the parent dialog/modal structure  
  const dialogInfo = await page.locator('[role="dialog"]').first().evaluate(el => {
    const r = el.getBoundingClientRect();
    return {tagName: el.tagName, role: el.getAttribute('role'), rect:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}};
  }).catch(e=>'ERR:'+e.message);
  console.log('Dialog:', JSON.stringify(dialogInfo));

  await browser.close();
})();
