const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function main() {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const contexts = browser.contexts();
  const context = contexts[0];
  
  const rankPage = await context.newPage();
  
  // Check today's launches
  console.log('Checking todays launches...');
  await rankPage.goto('https://www.producthunt.com/leaderboard/daily/2026/3/26', { waitUntil: 'networkidle', timeout: 30000 });
  await sleep(3000);
  
  const pageText = await rankPage.evaluate(() => document.body.innerText);
  require('fs').writeFileSync('/Users/rickthebot/.openclaw/workspace/ph-ranking-page.txt', pageText);
  
  console.log('Ranking page text:');
  console.log(pageText.substring(0, 3000));
  
  await rankPage.screenshot({ path: '/Users/rickthebot/.openclaw/workspace/ph-ranking-screenshot.png' });
  await rankPage.close();
  
  await browser.close();
}

main().catch(err => {
  console.error('Fatal:', err);
  process.exit(1);
});
