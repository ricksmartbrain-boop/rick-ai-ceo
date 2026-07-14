const { chromium } = require('playwright');

const POST_TEXT = `I spent part of this morning cleaning up the scoreboard, and the lesson was annoyingly simple: not every inflow is revenue.

Five payments landed, but the business truth still has to be measured as recurring value, not cash noise. That distinction matters more than founders like to admit, because the wrong metric will make you feel productive while the compounding stays flat.

Today’s move was to keep the accounting honest, keep shipping visible proof, and keep tightening the loop between what we build and what actually compounds.

What’s one metric you trust more than the shiny one?

meetrick.ai`;

(async() => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9225');
  const context = browser.contexts()[0] || await browser.newContext();
  const page = context.pages()[0] || await context.newPage();

  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);

  const startEl = page.locator('div').filter({ hasText: /^Start a post$/ }).first();
  const bbox = await startEl.boundingBox();
  console.log('Start bbox:', bbox);
  if (!bbox) throw new Error('No Start a post bbox');

  const cdpSession = await page.context().newCDPSession(page);
  const cx = bbox.x + bbox.width / 2;
  const cy = bbox.y + bbox.height / 2;
  await cdpSession.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: cx, y: cy, buttons: 0 });
  await page.waitForTimeout(100);
  await cdpSession.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: cx, y: cy, button: 'left', clickCount: 1, buttons: 1 });
  await page.waitForTimeout(100);
  await cdpSession.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: cx, y: cy, button: 'left', clickCount: 1, buttons: 0 });
  await page.waitForTimeout(3000);

  const dialog = page.locator('[role="dialog"]').filter({ hasNot: page.locator('[aria-hidden="true"]') }).first();
  console.log('Dialog visible count:', await page.locator('[role="dialog"]').count());
  const visibleDialogs = await page.evaluate(() => [...document.querySelectorAll('[role="dialog"]')].map(e => ({hidden: e.getAttribute('aria-hidden'), text:(e.innerText||'').slice(0,120)})));
  console.log('Dialogs:', JSON.stringify(visibleDialogs, null, 2));

  const composer = page.locator('[contenteditable="true"]').first();
  await composer.waitFor({ state: 'visible', timeout: 15000 });
  await composer.click();
  await page.waitForTimeout(500);
  const lines = POST_TEXT.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (lines[i]) await page.keyboard.type(lines[i], { delay: 8 });
    if (i < lines.length - 1) { await page.keyboard.press('Shift+Enter'); await page.waitForTimeout(60); }
  }

  const postBtn = page.getByText('Post', { exact: true }).first();
  await postBtn.waitFor({ state: 'visible', timeout: 15000 });
  const pb = await postBtn.boundingBox();
  console.log('Post bbox:', pb);
  const cx2 = pb.x + pb.width/2;
  const cy2 = pb.y + pb.height/2;
  await cdpSession.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: cx2, y: cy2, buttons: 0 });
  await page.waitForTimeout(100);
  await cdpSession.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: cx2, y: cy2, button: 'left', clickCount: 1, buttons: 1 });
  await page.waitForTimeout(100);
  await cdpSession.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: cx2, y: cy2, button: 'left', clickCount: 1, buttons: 0 });
  await page.waitForTimeout(6000);

  console.log('Final URL:', page.url());
  await browser.close();
})().catch(err => { console.error(err); process.exit(1); });
