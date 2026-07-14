const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9225');
  const contexts = browser.contexts();
  const context = contexts[0] || await browser.newContext();
  
  let page = context.pages()[0];
  if (!page) {
    page = await context.newPage();
  }
  
  await page.goto('https://www.producthunt.com/posts/rick', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(4000);
  
  // Scroll through entire page to load all comments
  for (let i = 0; i < 5; i++) {
    const scrollStep = i + 1;
    await page.evaluate((args) => window.scrollTo(0, document.body.scrollHeight * (args.step / args.total)), { step: scrollStep, total: 5 });
    await page.waitForTimeout(1500);
  }
  
  const currentUrl = page.url();
  console.log('URL:', currentUrl);
  
  // Get upvote count
  const upvoteText = await page.evaluate(() => {
    const voteEl = document.querySelector('[class*="vote" i]');
    return voteEl?.textContent?.trim();
  });
  console.log('Upvote area text:', upvoteText);
  
  // Get all comment blocks with their structure - look for nested reply structure
  const fullCommentTree = await page.evaluate(() => {
    // Find all user profile links - these anchor comments
    const allUserLinks = [...document.querySelectorAll('a[href*="/@"]')];
    
    // Build a comment map by finding parent containers
    const commentContainers = [];
    
    // Try to find comment "root" elements - they typically contain an author + text + reply button
    // PH comments usually have a specific structure
    const possibleComments = [...document.querySelectorAll('div')].filter(div => {
      const links = div.querySelectorAll('a[href*="/@"]');
      const hasUserLink = links.length > 0;
      const hasParagraph = div.querySelector('p');
      const ownChildren = div.children.length;
      // Comments are containers that have exactly 1 user link (the author) and some text
      // We want containers that are small but meaningful
      return hasUserLink && hasParagraph && ownChildren >= 1 && ownChildren <= 5;
    });
    
    const comments = possibleComments.slice(0, 30).map(el => {
      const userLinks = [...el.querySelectorAll('a[href*="/@"]')];
      const usernames = userLinks.map(a => a.getAttribute('href'));
      const paragraphs = [...el.querySelectorAll('p')].map(p => p.textContent.trim());
      const hasReplyButton = !!el.querySelector('button[class*="reply" i], button[data-test*="reply" i]');
      
      return {
        usernames,
        text: paragraphs.join(' | ').substring(0, 300),
        hasReplyButton,
        classes: el.className.substring(0, 80),
      };
    });
    
    // Deduplicate by text
    const seen = new Set();
    return comments.filter(c => {
      const key = c.usernames.join(',') + c.text.substring(0, 50);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  });
  
  console.log('Full comment tree:');
  fullCommentTree.forEach((c, i) => {
    console.log(`\n--- Comment ${i+1} ---`);
    console.log('Authors:', c.usernames);
    console.log('Text:', c.text.substring(0, 200));
    console.log('Has reply btn:', c.hasReplyButton);
  });
  
  // Check if we're logged in as meetrickai
  const loggedInUser = await page.evaluate(() => {
    // Look for logged-in user indicators
    const myLinks = [...document.querySelectorAll('a[href="/@meetrickai"]')];
    const profileLink = document.querySelector('[data-test="user-menu"], [aria-label*="profile" i], [aria-label*="account" i]');
    return {
      meetrickaiLinks: myLinks.length,
      profileLinkText: profileLink?.textContent?.trim(),
      hasCommentForm: !!document.querySelector('form textarea, form [contenteditable]'),
    };
  });
  console.log('\nLogged in info:', JSON.stringify(loggedInUser));
  
  await browser.close();
})().catch(err => {
  console.error('ERROR:', err.message);
  process.exit(1);
});
