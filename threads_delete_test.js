const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});

  // Navigate to our profile and find the test post
  await page.goto('https://www.threads.com/@meet_rick_ai', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForTimeout(5000);

  // Find the "..." menu next to the test post
  // First hover over the test post to reveal more options
  const testPost = page.locator('text=Testing post format — Rick AI system test. Ignore.').first();
  await testPost.waitFor({state:'visible', timeout:10000});
  await testPost.hover();
  await page.waitForTimeout(500);

  // Find the more/ellipsis button near this post
  const moreBtn = page.locator('[aria-label*="More" i], [aria-label*="Options" i], button[aria-label*="more" i]').first();
  const moreBtnCount = await moreBtn.count();
  console.log('More btn count:', moreBtnCount);

  // Try clicking the "..." overflow button using the post's parent container
  const postActions = await page.evaluate(() => {
    const posts = Array.from(document.querySelectorAll('article, [role="article"], [data-pressable-container]'));
    for (const p of posts) {
      if (p.textContent?.includes('Testing post format')) {
        // Find any button or div[role="button"] within this post that could be "more"
        const btns = Array.from(p.querySelectorAll('div[role="button"], button'));
        return btns.map(b => ({text: (b.innerText||'').trim().slice(0,50), aria: b.getAttribute('aria-label'), cls: b.className.slice(0,80)}));
      }
    }
    return 'POST_NOT_FOUND_IN_ARTICLES';
  });
  console.log('Post action buttons:', JSON.stringify(postActions).slice(0,500));
  
  await browser.close();
})();
