const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function main() {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const contexts = browser.contexts();
  const context = contexts[0];
  
  const rankPage = await context.newPage();
  
  // March 20 was 6 days ago
  await rankPage.goto('https://www.producthunt.com/leaderboard/daily/2026/3/20/all', { waitUntil: 'networkidle', timeout: 30000 });
  await sleep(3000);
  
  const text = await rankPage.evaluate(() => document.body.innerText);
  require('fs').writeFileSync('/Users/rickthebot/.openclaw/workspace/ph-march20.txt', text);
  
  console.log('March 20 page (first 4000 chars):');
  console.log(text.substring(0, 4000));

  // Also try fetching the post page to get ranking from there
  await rankPage.goto('https://www.producthunt.com/posts/rick', { waitUntil: 'networkidle', timeout: 30000 });
  await sleep(3000);
  
  // Look for ranking badge
  const rankInfo = await rankPage.evaluate(() => {
    // Look for #1, #2, etc ranking badges or text
    const allText = document.body.innerText;
    // Look for patterns like "#5 Product of the Day"
    const rankMatches = allText.match(/#\d+\s+(?:Product|Top)/gi) || [];
    const rankBadges = document.querySelectorAll('[class*="rank"], [class*="badge"], [class*="award"]');
    const badgeTexts = Array.from(rankBadges).map(el => el.innerText || el.textContent).filter(t => t.trim().length > 0);
    return { rankMatches, badgeTexts, fullText: allText.substring(0, 2000) };
  });
  
  console.log('\nRank info from post page:');
  console.log(JSON.stringify(rankInfo, null, 2));
  
  await rankPage.close();
  await browser.close();
}

main().catch(err => {
  console.error('Fatal:', err);
  process.exit(1);
});
