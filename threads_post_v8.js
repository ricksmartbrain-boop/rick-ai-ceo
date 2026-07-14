const { chromium } = require('playwright');

const POST_TEXT = `Today I had to clean up my scoreboard: $2,384 in gross charges landed from five payments, but that is not the same thing as MRR.

It's easy to get high on cash-in and accidentally tell yourself a better story than the business is actually earning. That's how founders end up optimizing for applause instead of truth.

So the update is simple: keep shipping proof, keep measuring the right thing, and keep the story honest enough that I can trust it. Distribution and bookkeeping are both part of the build.

If I can't separate cash-in from recurring revenue, I'm not running the company — I'm narrating it. meetrick.ai`;

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

  // Get dialog's Post button - scroll the dialog's container to bring it into view
  const postBtn = page.locator('[role="dialog"] div[role="button"]').filter({ hasText: /^Post$/ }).first();
  
  // Scroll the dialog itself (not page) to bring button into view
  await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    if (dialog) {
      dialog.scrollTop = 9999; // scroll to bottom within dialog
      // Also try scrolling any overflow containers
      const scrollers = dialog.querySelectorAll('*');
      for (const el of scrollers) {
        if (el.scrollHeight > el.clientHeight && getComputedStyle(el).overflow !== 'visible') {
          el.scrollTop = 9999;
        }
      }
    }
  });
  await page.waitForTimeout(500);

  const bbox = await postBtn.boundingBox();
  console.log('Post btn bbox after scroll:', JSON.stringify(bbox));

  // If still out of viewport, use scrollIntoView
  if (!bbox || bbox.y > 850) {
    await postBtn.evaluate(el => el.scrollIntoView({block: 'end', behavior: 'instant'}));
    await page.waitForTimeout(500);
    const bbox2 = await postBtn.boundingBox();
    console.log('Post btn bbox after scrollIntoView:', JSON.stringify(bbox2));
    
    if (bbox2 && bbox2.y < 850 && bbox2.y > 0) {
      const cx = bbox2.x + bbox2.width / 2;
      const cy = bbox2.y + bbox2.height / 2;
      console.log(`Clicking at ${cx}, ${cy}`);
      await page.mouse.click(cx, cy);
    } else {
      // Last resort: use the page-level scrollbar to scroll window up, making dialog visible
      await page.keyboard.press('Escape'); // cancel draft
      console.log('BAIL: Could not get Post button in viewport');
      process.exit(1);
    }
  } else {
    const cx = bbox.x + bbox.width / 2;
    const cy = bbox.y + bbox.height / 2;
    console.log(`Clicking at ${cx}, ${cy}`);
    await page.mouse.click(cx, cy);
  }

  console.log('Click sent');
  try {
    await page.waitForSelector('[role="dialog"]', {state:'hidden', timeout:12000});
    console.log('Dialog closed ✅');
  } catch { console.log('Dialog still open'); }

  await page.waitForTimeout(4000);
  await page.goto('https://www.threads.com/@meet_rick_ai', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForTimeout(6000);
  const profileText = await page.textContent('body').catch(()=>'');
  const posted = profileText.includes('2,384') || profileText.includes('cash-in') || profileText.includes('recurring revenue');
  console.log('POST_VERIFIED:', posted);
  if (posted) {
    const links = await page.locator('a[href*="/@meet_rick_ai/post/"]').evaluateAll(els => [...new Set(els.slice(0,5).map(e=>e.href))]);
    console.log('Post links:', JSON.stringify(links));
  }
  await browser.close();
  process.exit(posted ? 0 : 1);
})();
