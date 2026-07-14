const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9225');
  const contexts = browser.contexts();
  const context = contexts[0] || await browser.newContext();
  
  let page = context.pages()[0];
  if (!page) page = await context.newPage();
  
  await page.goto('https://www.producthunt.com/posts/rick', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(4000);
  
  // Scroll to load all comments
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(3000);
  
  // Get precise upvote count
  const upvotes = await page.evaluate(() => {
    // Find the vote button/counter
    const voteButton = document.querySelector('[class*="vote" i]');
    const match = voteButton?.textContent?.match(/\d+/);
    return match ? parseInt(match[0]) : null;
  });
  
  // Get the top-level comments (not replies) by finding the shallowest comment containers
  // A top-level comment has the author's link and text but is NOT nested inside another comment
  const topLevelComments = await page.evaluate(() => {
    // Find all elements that look like they contain a single comment author + text
    // and check if they appear to have a reply from meetrickai as a sibling/child
    
    const results = [];
    
    // PH comment threads: look for comment-thread-like structures
    // The key insight: a top-level comment from a non-Rick user that is NOT followed by 
    // a meetrickai reply is unanswered
    
    // Get all paragraph texts and their closest user link
    const paragraphs = [...document.querySelectorAll('p')];
    const commentPairs = [];
    
    for (const p of paragraphs) {
      const text = p.textContent.trim();
      if (text.length < 15 || text.length > 600) continue;
      
      // Find nearest ancestor that has a user link
      let el = p.parentElement;
      let authorLink = null;
      for (let i = 0; i < 5; i++) {
        if (!el) break;
        authorLink = el.querySelector('a[href*="/@"]');
        if (authorLink) break;
        el = el.parentElement;
      }
      
      if (authorLink) {
        const author = authorLink.getAttribute('href').replace('/@', '').replace('https://www.producthunt.com/', '');
        commentPairs.push({ author, text: text.substring(0, 250) });
      }
    }
    
    // Deduplicate
    const seen = new Set();
    return commentPairs.filter(c => {
      const key = c.author + '::' + c.text.substring(0, 80);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  });
  
  console.log('Upvote count:', upvotes);
  console.log('\nAll comment-like content:');
  topLevelComments.forEach((c, i) => {
    console.log(`${i+1}. @${c.author}: "${c.text.substring(0, 150)}"`);
  });
  
  // Check explicitly: is there ANY comment from a non-meetrickai user that doesn't
  // have a corresponding reply visible on the page?
  const nonRickComments = topLevelComments.filter(c => c.author !== 'meetrickai');
  console.log('\nNon-Rick comments:', nonRickComments.length);
  
  const pageText = await page.evaluate(() => document.body.innerText);
  
  for (const comment of nonRickComments) {
    const authorShort = comment.author.replace('https://www.producthunt.com/@', '');
    // Check if meetrickai has a reply mentioning this user
    const replyExists = pageText.includes('@' + authorShort) && 
      pageText.indexOf('@meetrickai') > -1;
    console.log(`\nComment from @${authorShort}:`);
    console.log('  Text:', comment.text.substring(0, 100));
    console.log('  Has @mention reply:', replyExists);
  }
  
  await browser.close();
})().catch(err => {
  console.error('ERROR:', err.message);
  process.exit(1);
});
