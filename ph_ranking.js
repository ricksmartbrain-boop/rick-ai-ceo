const CDP = require('chrome-remote-interface');

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function run() {
  let client;
  try {
    client = await CDP({ port: 9222 });
    const { Page, Runtime } = client;
    await Page.enable();
    await Runtime.enable();
    
    // Check yesterday's launches for Rick's ranking
    console.log('Checking PH launches page...');
    await Page.navigate({ url: 'https://www.producthunt.com/leaderboard/daily/2026/3/25' });
    await sleep(6000);
    
    const result = await Runtime.evaluate({
      expression: `
        (function() {
          // Get all products listed
          const items = [...document.querySelectorAll('[data-test^="post-item-"], [class*="product-item"], [class*="postItem"]')];
          
          // Fallback: get all links to /posts/
          const postLinks = [...document.querySelectorAll('a[href*="/posts/"]')];
          const products = postLinks.map((a, i) => ({
            href: a.getAttribute('href'),
            text: a.textContent.trim().substring(0, 100)
          })).filter(p => p.text.length > 2);
          
          // Look for Rick specifically
          const rickEl = [...document.querySelectorAll('*')].find(el => 
            el.children.length === 0 && el.textContent.trim() === 'Rick'
          );
          
          return {
            title: document.title,
            url: window.location.href,
            products: products.slice(0, 30),
            rickFound: !!rickEl,
            rickContext: rickEl ? rickEl.closest('[data-test], article, li')?.textContent?.trim().substring(0, 200) : null,
            bodyText: document.body.textContent.substring(0, 5000)
          };
        })()
      `,
      returnByValue: true
    });
    
    const d = result.result.value;
    console.log('Title:', d.title);
    console.log('URL:', d.url);
    console.log('Rick found:', d.rickFound);
    if (d.rickContext) console.log('Rick context:', d.rickContext);
    
    // Search for Rick in body text
    const idx = d.bodyText.indexOf('Rick');
    if (idx > -1) {
      console.log('Rick in body at pos', idx, ':', d.bodyText.substring(Math.max(0, idx-50), idx+200));
    }
    
    console.log('\nFirst 20 product links:');
    d.products?.slice(0, 20).forEach((p, i) => console.log(i+1, p.href, '|', p.text.substring(0, 60)));
    
  } catch(e) {
    console.error('Error:', e.message);
  } finally {
    if (client) await client.close();
  }
}

run();
