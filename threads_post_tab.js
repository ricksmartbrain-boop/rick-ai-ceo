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

  // Tab to the Post button from the composer
  // First find how many tabs needed to reach Post button
  let tabCount = 0;
  for (let t = 0; t < 15; t++) {
    await page.keyboard.press('Tab');
    tabCount++;
    const focused = await page.evaluate(() => {
      const el = document.activeElement;
      return el ? `${el.tagName}|role=${el.getAttribute('role')}|text=${(el.innerText||'').trim().slice(0,50)}` : 'null';
    });
    console.log(`Tab ${tabCount}: ${focused}`);
    if (focused.includes('Post')) {
      console.log('Found Post button via Tab!');
      await page.keyboard.press('Enter');
      break;
    }
  }

  await page.waitForTimeout(6000);
  const afterText = await composer.textContent().catch(()=>'ELEMENT_GONE');
  console.log('Composer after:', afterText === 'ELEMENT_GONE' || !afterText.trim() ? 'CLEARED ✅' : 'STILL HAS TEXT');
  console.log('URL after:', page.url());

  await browser.close();
})();
