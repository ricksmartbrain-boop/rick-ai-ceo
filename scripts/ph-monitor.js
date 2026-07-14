/**
 * PH Comment Monitor via CDP Chrome (port 9222)
 * Uses Playwright to connect to existing Chrome instance
 */

const { chromium } = require('playwright');

const CDP_PORT = 9222;
const PH_POST_URL = 'https://www.producthunt.com/posts/rick';

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

(async () => {
  let browser;
  try {
    browser = await chromium.connectOverCDP(`http://localhost:${CDP_PORT}`);
  } catch (e) {
    console.error('CDP_CONNECT_FAIL:', e.message);
    process.exit(1);
  }

  const contexts = browser.contexts();
  const context = contexts[0];
  
  // Open new page
  const page = await context.newPage();
  
  console.log('Navigating to PH post...');
  await page.goto(PH_POST_URL, { waitUntil: 'networkidle', timeout: 30000 });
  await sleep(3000);
  
  // Scroll to load all comments
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(2000);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(2000);

  // Get page HTML for debugging
  const pageTitle = await page.title();
  console.log('PAGE_TITLE:', pageTitle);

  // Try to get upvote count
  const upvoteCount = await page.evaluate(() => {
    // Look for vote count - PH uses various selectors
    const selectors = [
      '[data-test="vote-button"] span',
      '[class*="voteCount"]',
      '[class*="vote_count"]',
      'button[class*="vote"] span',
    ];
    for (const sel of selectors) {
      const els = document.querySelectorAll(sel);
      for (const el of els) {
        const num = parseInt(el.textContent.trim());
        if (!isNaN(num) && num > 0) return num;
      }
    }
    // Broader search
    const allButtons = document.querySelectorAll('button');
    for (const btn of allButtons) {
      const text = btn.textContent.trim();
      if (/^\d+$/.test(text) && parseInt(text) > 10) return parseInt(text);
    }
    return null;
  });
  console.log('UPVOTE_COUNT:', upvoteCount);

  // Extract all comments
  const comments = await page.evaluate(() => {
    const results = [];
    
    // PH comment structure - try multiple selectors
    const commentContainers = document.querySelectorAll(
      '[data-test="comment"], [class*="comment-body"], [class*="CommentItem"], [class*="comment_item"]'
    );
    
    if (commentContainers.length === 0) {
      // Fallback: look for comment-like sections
      const sections = document.querySelectorAll('section, article, [role="article"]');
      console.log('sections found:', sections.length);
    }

    commentContainers.forEach((node, idx) => {
      const authorLinks = node.querySelectorAll('a[href*="/@"], a[href*="/members/"]');
      const author = authorLinks[0] ? 
        (authorLinks[0].textContent.trim() || authorLinks[0].href.split('/').filter(Boolean).pop()) 
        : null;
      
      const textEl = node.querySelector('p, [class*="body"], [class*="text"], [class*="content"]');
      const text = textEl ? textEl.textContent.trim() : node.textContent.slice(0, 300).trim();
      
      if (!author || !text) return;
      
      // Check if this is from @meetrickai
      const isMaker = author.toLowerCase().includes('meetrickai') || 
                      node.querySelector('[class*="maker"], [data-test*="maker"]') !== null;
      
      // Check for replies under this comment
      const replyContainer = node.querySelector('[class*="replies"], [data-test*="replies"]');
      const replyAuthors = replyContainer ? 
        [...replyContainer.querySelectorAll('a[href*="/@"]')].map(a => a.textContent.trim().toLowerCase()) : [];
      const hasRickReply = replyAuthors.some(a => a.includes('meetrickai'));
      
      results.push({ idx, author, text: text.substring(0, 400), isMaker, hasRickReply, replyAuthors });
    });
    
    return results;
  });

  console.log('COMMENTS_FOUND:', comments.length);
  
  // If no comments found with standard selectors, dump page structure
  if (comments.length === 0) {
    const structure = await page.evaluate(() => {
      const body = document.body;
      // Get unique class names that contain 'comment'
      const all = document.querySelectorAll('*');
      const commentClasses = new Set();
      all.forEach(el => {
        if (el.className && typeof el.className === 'string') {
          el.className.split(' ').forEach(cls => {
            if (cls.toLowerCase().includes('comment')) commentClasses.add(cls);
          });
        }
      });
      return {
        commentClasses: [...commentClasses].slice(0, 20),
        bodyText: body.textContent.slice(0, 500)
      };
    });
    console.log('DEBUG_STRUCTURE:', JSON.stringify(structure));
    
    // Try screenshotting for debug
    await page.screenshot({ path: '/tmp/ph-debug.png', fullPage: false });
    console.log('Screenshot saved to /tmp/ph-debug.png');
  }

  const data = { upvoteCount, comments, pageTitle };
  process.stdout.write('\n__DATA__' + JSON.stringify(data) + '__DATA__\n');
  
  await page.close();
  await browser.close();
})();
