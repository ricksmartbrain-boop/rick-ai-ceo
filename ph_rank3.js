const CDP = require('chrome-remote-interface');

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function run() {
  let client;
  try {
    client = await CDP({ port: 9222 });
    const { Page, Runtime } = client;
    await Page.enable();
    await Runtime.enable();
    
    // Check yesterday's leaderboard (Mar 25 was launch day)
    console.log('Checking Mar 25 leaderboard...');
    await Page.navigate({ url: 'https://www.producthunt.com/leaderboard/daily/2026/3/25' });
    await sleep(8000);
    
    // Scroll to load all
    await Runtime.evaluate({ expression: 'window.scrollTo(0, document.body.scrollHeight)' });
    await sleep(2000);
    
    const result = await Runtime.evaluate({
      expression: `
        (function() {
          const text = document.body.innerText;
          return {
            title: document.title,
            url: window.location.href,
            bodyText: text.substring(0, 8000),
            rickIdx: text.indexOf('Rick'),
            hasRick: text.includes('Rick')
          };
        })()
      `,
      returnByValue: true
    });
    
    const d = result.result.value;
    console.log('Title:', d.title);
    console.log('URL:', d.url);
    console.log('Has Rick:', d.hasRick, 'at idx:', d.rickIdx);
    if (d.rickIdx > -1) {
      console.log('Context around Rick:', d.bodyText.substring(Math.max(0, d.rickIdx - 100), d.rickIdx + 300));
    }
    
    // Show first 3000 chars of body text to see ranking structure
    console.log('\nLeaderboard body text (first 3000):');
    console.log(d.bodyText.substring(0, 3000));
    
  } catch(e) {
    console.error('Error:', e.message);
  } finally {
    if (client) await client.close();
  }
}

run();
