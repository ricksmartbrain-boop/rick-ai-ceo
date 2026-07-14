const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9225');
  const contexts = browser.contexts();
  const context = contexts[0] || await browser.newContext();
  
  // Open or reuse a page
  let page = context.pages().find(p => p.url().includes('producthunt')) || context.pages()[0];
  if (!page) {
    page = await context.newPage();
  }
  
  console.log('Navigating to PH post page...');
  await page.goto('https://www.producthunt.com/posts/rick', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3000);
  
  // Get upvote count
  const upvoteCount = await page.evaluate(() => {
    // Try various selectors for upvote count
    const selectors = [
      '[data-test="vote-button"] span',
      'button[aria-label*="upvote" i] span',
      '[class*="vote"] [class*="count"]',
      '[class*="VoteButton"] span',
      'button[class*="vote"] span',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && /\d/.test(el.textContent)) return el.textContent.trim();
    }
    // Try finding by text content near vote area
    const buttons = [...document.querySelectorAll('button')];
    for (const btn of buttons) {
      const text = btn.textContent.trim();
      if (/^\d+$/.test(text) && parseInt(text) > 0) return text;
    }
    return 'unknown';
  });
  
  console.log('Upvote count:', upvoteCount);
  
  // Get page title to verify we're in the right place
  const title = await page.title();
  console.log('Page title:', title);
  
  // Get all comments
  const commentData = await page.evaluate(() => {
    // Try to find comment containers
    const results = [];
    
    // Look for comment elements - PH uses various structures
    const commentSelectors = [
      '[data-test="comment"]',
      '[class*="comment" i]',
      '[class*="Comment"]',
    ];
    
    let commentEls = [];
    for (const sel of commentSelectors) {
      const els = document.querySelectorAll(sel);
      if (els.length > 0) {
        commentEls = [...els];
        console.log('Found', els.length, 'elements with selector:', sel);
        break;
      }
    }
    
    // Parse each comment
    for (const el of commentEls) {
      const usernameEl = el.querySelector('[data-test="username"], [class*="username" i], a[href*="/@"]');
      const textEl = el.querySelector('p, [class*="body" i], [class*="content" i]');
      
      if (usernameEl || textEl) {
        const username = usernameEl?.textContent?.trim() || usernameEl?.getAttribute('href') || '';
        const text = textEl?.textContent?.trim() || '';
        const hasReplyBtn = !!el.querySelector('button, a[class*="reply" i]');
        
        results.push({ username, text: text.substring(0, 200), hasReplyBtn, html: el.innerHTML.substring(0, 500) });
      }
    }
    
    return results;
  });
  
  console.log('Comment data found:', JSON.stringify(commentData.slice(0, 5), null, 2));
  
  // Take a screenshot to see current state
  await page.screenshot({ path: '/Users/rickthebot/.openclaw/workspace/ph-screenshot.png', fullPage: false });
  console.log('Screenshot saved');
  
  // Get more detailed page structure
  const pageStructure = await page.evaluate(() => {
    const info = {
      url: window.location.href,
      commentCount: 0,
      upvotes: null,
      comments: [],
    };
    
    // Try different approaches to find upvotes
    const allButtons = [...document.querySelectorAll('button')];
    for (const btn of allButtons) {
      const text = btn.textContent.trim();
      const ariaLabel = btn.getAttribute('aria-label') || '';
      if (ariaLabel.toLowerCase().includes('upvote') || ariaLabel.toLowerCase().includes('vote')) {
        info.upvotes = { text, ariaLabel };
        break;
      }
    }
    
    // Find number spans near vote buttons
    const voteAreas = document.querySelectorAll('[class*="vote" i], [data-test*="vote"]');
    if (voteAreas.length > 0) {
      const firstVote = voteAreas[0];
      info.voteAreaText = firstVote.textContent.trim();
      info.voteAreaClass = firstVote.className;
    }
    
    // Find any numbers that could be upvotes (usually the first large number on page)
    const spans = [...document.querySelectorAll('span, div')].filter(el => {
      const text = el.textContent.trim();
      return /^\d{1,4}$/.test(text) && el.children.length === 0;
    });
    info.numericSpans = spans.slice(0, 10).map(s => ({ text: s.textContent.trim(), class: s.className }));
    
    return info;
  });
  
  console.log('Page structure:', JSON.stringify(pageStructure, null, 2));
  
  await browser.close();
})().catch(err => {
  console.error('ERROR:', err.message);
  process.exit(1);
});
