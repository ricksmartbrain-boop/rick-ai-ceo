const { chromium } = require('playwright');
const POST_TEXT = `Test short post 2.`;
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
  await page.keyboard.type('A'.repeat(200) + '\n' + 'B'.repeat(200) + '\n' + 'C'.repeat(100), {delay:5});
  await page.waitForTimeout(1000);

  const info = await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll('div[role="button"]'));
    const postBtn = buttons.find(b => b.innerText?.trim() === 'Post');
    if (!postBtn) return {error: 'NO POST BTN'};
    const r = postBtn.getBoundingClientRect();
    const cx = r.x + r.width/2, cy = r.y + r.height/2;
    const elAtPoint = document.elementFromPoint(cx, cy);
    const elsAtPoint = document.elementsFromPoint(cx, cy);
    return {
      btnRect: {x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height)},
      elAtPoint: elAtPoint ? elAtPoint.tagName + '|' + elAtPoint.getAttribute('role') + '|cls:' + elAtPoint.className.slice(0,120) : null,
      elsAtPoint: elsAtPoint.slice(0,6).map(e => e.tagName + '|role=' + e.getAttribute('role') + '|cls=' + e.className.slice(0,80)),
      viewportHeight: window.innerHeight,
    };
  });
  console.log(JSON.stringify(info, null, 2));
  await browser.close();
})();
