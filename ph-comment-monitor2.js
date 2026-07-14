const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9225');
  const contexts = browser.contexts();
  const context = contexts[0] || await browser.newContext();
  
  let page = context.pages()[0];
  if (!page) {
    page = await context.newPage();
  }
  
  console.log('Navigating to PH post page...');
  await page.goto('https://www.producthunt.com/posts/rick', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(4000);
  
  const currentUrl = page.url();
  console.log('Current URL:', currentUrl);
  
  // Scroll down to load comments
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight / 2));
  await page.waitForTimeout(2000);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(3000);
  
  // Get upvote count from the vote button
  const upvoteInfo = await page.evaluate(() => {
    const voteEl = document.querySelector('[class*="vote" i]');
    return {
      text: voteEl?.textContent?.trim(),
      class: voteEl?.className,
    };
  });
  console.log('Vote info:', JSON.stringify(upvoteInfo));
  
  // Find all comment-like structures
  const commentAnalysis = await page.evaluate(() => {
    // Try to find the comments section
    const sections = [...document.querySelectorAll('section, [class*="comment" i], [data-test*="comment"]')];
    const sectionInfo = sections.slice(0, 5).map(s => ({
      tag: s.tagName,
      class: s.className.substring(0, 100),
      id: s.id,
      childCount: s.children.length,
      text: s.textContent.substring(0, 100),
    }));
    
    // Find all links to user profiles (/@username)
    const userLinks = [...document.querySelectorAll('a[href*="/@"]')];
    const users = [...new Set(userLinks.map(a => a.getAttribute('href')))].slice(0, 20);
    
    // Find all text content that looks like a comment
    const paragraphs = [...document.querySelectorAll('p')].filter(p => {
      const text = p.textContent.trim();
      return text.length > 20 && text.length < 500;
    });
    const texts = paragraphs.slice(0, 10).map(p => p.textContent.trim().substring(0, 150));
    
    return { sectionInfo, users, texts };
  });
  
  console.log('Comment analysis:', JSON.stringify(commentAnalysis, null, 2));
  
  // Take a full-page screenshot
  await page.screenshot({ path: '/Users/rickthebot/.openclaw/workspace/ph-screenshot2.png', fullPage: true });
  console.log('Screenshot saved (full page)');
  
  await browser.close();
})().catch(err => {
  console.error('ERROR:', err.message);
  process.exit(1);
});
