const { chromium } = require('playwright');

// Short test post to capture the real mutation format
const TEST_TEXT = `Testing post format — Rick AI system test. Ignore.`;

(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForTimeout(3000);

  // Capture ALL graphql POSTs to find the create mutation
  const captured = [];
  page.on('request', req => {
    if (req.method() === 'POST' && (req.url().includes('graphql') || req.url().includes('api'))) {
      const pd = req.postData() || '';
      if (pd.includes('create') || pd.includes('Create') || pd.includes('thread') || pd.includes('Thread') || pd.includes('post') || pd.includes('Post')) {
        captured.push({url: req.url(), postData: pd.slice(0,1000)});
      }
    }
  });

  const createBtn = page.locator('a[href="/create"], [aria-label*="Create" i]').first();
  await createBtn.click({timeout:10000});
  await page.waitForTimeout(2000);

  const composer = page.locator('[role="dialog"] [contenteditable="true"]').first();
  await composer.waitFor({state:'visible', timeout:15000});
  await composer.click();
  await page.keyboard.type(TEST_TEXT, {delay: 8});
  await page.waitForTimeout(1000);

  console.log('Filled composer. Now clicking Post button via Playwright native click...');

  // Use Playwright's page.mouse with exact coordinates (not CDP, not evaluate)
  const bbox = await page.locator('[role="dialog"] div[role="button"]').filter({ hasText: /^Post$/ }).first().boundingBox();
  console.log('Post btn bbox:', JSON.stringify(bbox));

  if (bbox) {
    await page.mouse.click(bbox.x + bbox.width/2, bbox.y + bbox.height/2);
    await page.waitForTimeout(2000);

    // Also try clicking the inner child element
    const innerBbox = await page.locator('[role="dialog"] div[role="button"]').filter({ hasText: /^Post$/ }).first().locator('div').first().boundingBox();
    console.log('Inner div bbox:', JSON.stringify(innerBbox));
    if (innerBbox) {
      await page.mouse.click(innerBbox.x + innerBbox.width/2, innerBbox.y + innerBbox.height/2);
    }
  }

  await page.waitForTimeout(5000);
  console.log('Captured requests:', captured.length);
  captured.forEach(r => console.log(JSON.stringify(r)));

  const afterText = await composer.textContent().catch(()=>'ELEMENT_GONE');
  console.log('Composer after:', afterText === 'ELEMENT_GONE' || !afterText.trim() ? 'CLEARED' : 'STILL_HAS_TEXT');

  await browser.close();
})();
