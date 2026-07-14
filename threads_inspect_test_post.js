const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/@meet_rick_ai/post/DXR5TbaCnkz', {waitUntil:'networkidle', timeout:40000}).catch(()=>{});
  await page.waitForTimeout(5000);
  const items = await page.locator('div[role="button"], button, a').evaluateAll(els => els.slice(0,200).map((e,i)=>{
    const r = e.getBoundingClientRect();
    return {
      i,
      tag:e.tagName,
      role:e.getAttribute('role'),
      aria:e.getAttribute('aria-label'),
      text:(e.innerText||'').trim().slice(0,80),
      href:e.href||null,
      x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height)
    };
  }).filter(x=>x.aria||x.text||x.href));
  console.log(JSON.stringify(items,null,2));
  await browser.close();
})();
