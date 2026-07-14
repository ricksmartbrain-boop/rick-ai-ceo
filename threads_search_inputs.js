const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(3000);
  await page.getByText("What's new?").click({timeout:10000}).catch(e=>console.error('click failed', e.message));
  await page.waitForTimeout(1000);
  const counts = {
    textarea: await page.locator('textarea').count(),
    input: await page.locator('input').count(),
    contenteditable: await page.locator('[contenteditable="true"]').count(),
    prose: await page.locator('[role="textbox"]').count(),
    draft: await page.locator('[aria-label*="post" i]').count(),
  };
  console.log(counts);
  for (const sel of ['textarea','input','[contenteditable="true"]','[role="textbox"]']) {
    const n = await page.locator(sel).count();
    for (let i=0; i<Math.min(n,10); i++) {
      const html = await page.locator(sel).nth(i).evaluate(el=>el.outerHTML).catch(e=>'ERR:'+e.message);
      console.log('SEL', sel, 'IDX', i, html.slice(0,500));
    }
  }
  await browser.close();
})();
