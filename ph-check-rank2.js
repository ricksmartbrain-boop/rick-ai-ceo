const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function main() {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const contexts = browser.contexts();
  const context = contexts[0];
  
  const rankPage = await context.newPage();
  
  // Try all the days this week to find rick
  const dates = [
    '2026/3/20', '2026/3/21', '2026/3/22', '2026/3/23', '2026/3/24', '2026/3/25', '2026/3/26'
  ];
  
  for (const date of dates) {
    console.log(`\nChecking ${date}...`);
    await rankPage.goto(`https://www.producthunt.com/leaderboard/daily/${date}`, { waitUntil: 'networkidle', timeout: 30000 });
    await sleep(2000);
    const text = await rankPage.evaluate(() => document.body.innerText);
    if (text.toLowerCase().includes('rick') && (text.toLowerCase().includes('ai ceo') || text.toLowerCase().includes('meetrick'))) {
      console.log(`FOUND RICK on ${date}!`);
      // Extract ranking context
      const idx = text.toLowerCase().indexOf('ai ceo');
      if (idx > -1) {
        console.log(text.substring(Math.max(0, idx - 300), idx + 300));
      }
    } else {
      // Check if "rick" appears at all
      const lower = text.toLowerCase();
      const rickIdx = lower.indexOf('\nrick\n');
      if (rickIdx > -1) {
        console.log(`Found "rick" standalone on ${date}:`, text.substring(Math.max(0, rickIdx-200), rickIdx+300));
      } else {
        console.log(`Not found on ${date}`);
      }
    }
  }
  
  await rankPage.close();
  
  // Also try the posts/rick URL to get launch date from page
  const postPage = await context.newPage();
  await postPage.goto('https://www.producthunt.com/posts/rick', { waitUntil: 'networkidle', timeout: 30000 });
  await sleep(2000);
  const postText = await postPage.evaluate(() => document.body.innerText);
  // Find any date reference
  const dateMatch = postText.match(/\d+[dh]\s+ago|\w+ \d+, \d{4}/g);
  console.log('\nDate references on post:', dateMatch);
  await postPage.close();
  
  await browser.close();
}

main().catch(err => {
  console.error('Fatal:', err);
  process.exit(1);
});
