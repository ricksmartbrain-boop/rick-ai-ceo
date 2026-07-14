const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  // Hard reload to bypass cache
  await page.goto('https://www.threads.com/@meet_rick_ai', {waitUntil:'networkidle', timeout:40000});
  await page.waitForTimeout(7000);
  const profileText = await page.textContent('body').catch(()=>'');
  // Find all posts by looking for post links
  const postLinks = await page.locator('a[href*="/@meet_rick_ai/post/"]').evaluateAll(els => [...new Set(els.map(e=>e.href))]);
  console.log('Post links:', JSON.stringify(postLinks));
  // Check for specific phrases
  console.log('Has 2384:', profileText.includes('2,384'));
  console.log('Has cash-in:', profileText.includes('cash-in'));
  console.log('Has recurring:', profileText.includes('recurring revenue'));
  console.log('Has Testing:', profileText.includes('Testing post format'));
  // Print all visible text content between "Threads" section and "© 2026"
  const idx1 = profileText.indexOf('Threads\nReplies');
  const idx2 = profileText.indexOf('© 2026');
  const threadSection = profileText.slice(idx1 > 0 ? idx1 : 0, idx2 > 0 ? idx2 : 3000);
  console.log('\nPosts section:\n', threadSection.slice(0, 3000));
  await browser.close();
})();
