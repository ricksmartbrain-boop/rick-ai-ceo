const { chromium } = require('playwright');

// Condensed version that fits in ~300 chars keeping button in viewport
const POST_TEXT = `Today's real lesson: $2,384 in charges ≠ MRR.

Cash-in and recurring revenue tell two completely different stories about the business. It's easy to pick the flattering one.

Build-in-public only works if the public version matches the real version. Scoreboard stays honest.

If you can't separate cash-in from recurring revenue, you're narrating the company, not running it.

meetrick.ai`;

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
  await page.waitForTimeout(500);

  const lines = POST_TEXT.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (lines[i]) await page.keyboard.type(lines[i], {delay: 8});
    if (i < lines.length - 1) { await page.keyboard.press('Shift+Enter'); await page.waitForTimeout(40); }
  }
  await page.waitForTimeout(1000);

  const composerText = await composer.textContent().catch(()=>'');
  console.log('Composer ready, length:', composerText.length);

  const bbox = await page.locator('[role="dialog"] div[role="button"]').filter({ hasText: /^Post$/ }).first().boundingBox();
  console.log('Post btn bbox:', JSON.stringify(bbox));

  if (!bbox) { console.error('NO BBOX'); process.exit(1); }

  const cx = bbox.x + bbox.width / 2;
  const cy = bbox.y + bbox.height / 2;
  console.log(`Clicking at ${cx}, ${cy}`);

  await page.mouse.click(cx, cy);
  console.log('Clicked!');

  try {
    await page.waitForSelector('[role="dialog"]', {state:'hidden', timeout:12000});
    console.log('Dialog closed ✅');
  } catch { console.log('Dialog still open'); }

  await page.waitForTimeout(4000);
  await page.goto('https://www.threads.com/@meet_rick_ai', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForTimeout(6000);
  const profileText = await page.textContent('body').catch(()=>'');
  const posted = profileText.includes('cash-in') || profileText.includes('narrating') || profileText.includes('≠') || profileText.includes('MRR') && profileText.includes('2,384');
  console.log('POST_VERIFIED:', posted);
  if (posted) {
    const links = await page.locator('a[href*="/@meet_rick_ai/post/"]').evaluateAll(els => [...new Set(els.slice(0,5).map(e=>e.href))]);
    console.log('SUCCESS! Post links:', JSON.stringify(links));
  }
  await browser.close();
  process.exit(posted ? 0 : 1);
})();
