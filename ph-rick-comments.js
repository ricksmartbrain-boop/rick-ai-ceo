const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function main() {
  // Connect to CDP Chrome on port 9222
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  console.log('Connected to Chrome');

  const contexts = browser.contexts();
  const context = contexts[0] || await browser.newContext();
  
  // Get or create a page
  let page;
  const pages = context.pages();
  if (pages.length > 0) {
    page = pages[0];
  } else {
    page = await context.newPage();
  }

  // Navigate to the Rick post
  console.log('Navigating to PH post...');
  await page.goto('https://www.producthunt.com/posts/rick', { waitUntil: 'networkidle', timeout: 30000 });
  await sleep(3000);

  // Scroll down to load all comments
  console.log('Scrolling to load comments...');
  for (let i = 0; i < 10; i++) {
    await page.keyboard.press('End');
    await sleep(1500);
  }
  await sleep(2000);

  // Get upvote count
  let upvoteCount = 'unknown';
  try {
    // Try various selectors for upvote count
    const upvoteEl = await page.$('[data-test="vote-button"] span, .vote-count, [class*="voteCount"], [class*="vote-count"]');
    if (upvoteEl) {
      upvoteCount = await upvoteEl.textContent();
    }
    // Also try by aria
    const voteButtons = await page.$$('button[data-test*="vote"], button[aria-label*="upvote"], button[aria-label*="vote"]');
    for (const btn of voteButtons) {
      const text = await btn.textContent();
      if (/^\d+$/.test(text.trim())) {
        upvoteCount = text.trim();
        break;
      }
    }
  } catch(e) {
    console.log('Could not get upvote count:', e.message);
  }
  console.log('Upvote count:', upvoteCount);

  // Get page HTML for analysis
  const html = await page.content();
  
  // Extract comments using JS evaluation
  console.log('Extracting comments...');
  const commentsData = await page.evaluate(() => {
    const results = [];
    
    // Try to find comment elements - PH uses various class patterns
    // Look for comment containers
    const commentSelectors = [
      '[data-test="comment"]',
      '[class*="comment"]',
      'div[class*="Comment"]',
      'li[class*="comment"]',
    ];
    
    let commentEls = [];
    for (const sel of commentSelectors) {
      const els = document.querySelectorAll(sel);
      if (els.length > 0) {
        commentEls = Array.from(els);
        console.log(`Found ${els.length} elements with selector: ${sel}`);
        break;
      }
    }
    
    // Try to find by structure - comments usually have author + text
    if (commentEls.length === 0) {
      // Look for any div that has a username and comment text
      const allDivs = document.querySelectorAll('div[class]');
      commentEls = Array.from(allDivs).filter(el => {
        const text = el.textContent || '';
        const hasAt = text.includes('@');
        const className = el.className || '';
        return className.toLowerCase().includes('comment') || 
               (el.children.length >= 2 && text.length > 20);
      });
    }
    
    return {
      pageTitle: document.title,
      commentCount: commentEls.length,
      // Get all text content from main discussion area
      bodyText: document.body.innerText.substring(0, 5000),
    };
  });
  
  console.log('Page title:', commentsData.pageTitle);
  console.log('Body text preview:', commentsData.bodyText.substring(0, 1000));

  // Take screenshot for debugging
  await page.screenshot({ path: '/Users/rickthebot/.openclaw/workspace/ph-rick-debug.png', fullPage: true });
  console.log('Screenshot saved');

  // Try a more targeted approach - get all text visible on page
  const pageText = await page.evaluate(() => {
    // Find discussion/comments section
    const sections = ['discussion', 'comments', 'comment'];
    for (const s of sections) {
      const el = document.querySelector(`[id*="${s}"], [class*="${s}"], section[class*="${s}"]`);
      if (el) return el.innerText;
    }
    return document.body.innerText;
  });

  // Now let's use a smarter approach - look at all anchor tags with usernames
  const commentsList = await page.evaluate(() => {
    const comments = [];
    
    // PH comments often have: author link + comment body
    // Look for patterns like @username in links
    const userLinks = Array.from(document.querySelectorAll('a[href*="/@"]'));
    
    // Find comment containers by looking at paragraphs near user links
    const visited = new Set();
    
    for (const link of userLinks) {
      const href = link.getAttribute('href') || '';
      const username = href.replace('/@', '').split('/')[0];
      if (!username || username.length < 2) continue;
      
      // Get closest container that might be a comment
      let container = link.parentElement;
      for (let i = 0; i < 5; i++) {
        if (!container) break;
        const className = (container.className || '').toLowerCase();
        if (className.includes('comment') || className.includes('discussion') || 
            container.tagName === 'LI' || container.tagName === 'ARTICLE') {
          break;
        }
        container = container.parentElement;
      }
      
      if (!container || visited.has(container)) continue;
      visited.add(container);
      
      const text = container.innerText || '';
      if (text.length < 10) continue;
      
      comments.push({
        username,
        containerText: text.substring(0, 500),
        hasReplyFromMeetrickai: text.toLowerCase().includes('meetrickai') || 
                                 text.includes('@meetrickai'),
      });
    }
    
    return comments;
  });
  
  console.log('\n=== COMMENTS FOUND ===');
  console.log(JSON.stringify(commentsList, null, 2));

  // Check current ranking on homepage
  console.log('\nChecking homepage ranking...');
  const rankingPage = await context.newPage();
  await rankingPage.goto('https://www.producthunt.com', { waitUntil: 'networkidle', timeout: 30000 });
  await sleep(3000);
  
  const rankingData = await rankingPage.evaluate(() => {
    const products = Array.from(document.querySelectorAll('[data-test="product-item"], li[class*="item"]'));
    const results = [];
    for (let i = 0; i < Math.min(products.length, 20); i++) {
      const text = (products[i].innerText || '').toLowerCase();
      if (text.includes('rick')) {
        results.push({ rank: i + 1, text: text.substring(0, 200) });
      }
    }
    // Also get all product names visible
    const allText = document.body.innerText;
    const rickIdx = allText.toLowerCase().indexOf('rick');
    if (rickIdx > -1) {
      results.push({ contextText: allText.substring(Math.max(0, rickIdx - 100), rickIdx + 200) });
    }
    return results;
  });
  
  console.log('\n=== RANKING DATA ===');
  console.log(JSON.stringify(rankingData, null, 2));
  
  await rankingPage.screenshot({ path: '/Users/rickthebot/.openclaw/workspace/ph-ranking-debug.png' });
  await rankingPage.close();

  // Go back to post page to handle replies
  await page.bringToFront();
  
  // Save full page text for analysis
  const fullPageText = await page.evaluate(() => document.body.innerText);
  require('fs').writeFileSync('/Users/rickthebot/.openclaw/workspace/ph-page-text.txt', fullPageText);
  console.log('\nFull page text saved to ph-page-text.txt');

  await browser.close();
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
