const CDP = require('chrome-remote-interface');

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function run() {
  let client;
  try {
    client = await CDP({ port: 9222 });
    const { Page, Runtime } = client;
    await Page.enable();
    await Runtime.enable();
    
    // Navigate directly to the post to get vote count via the page
    console.log('Checking post page vote count...');
    await Page.navigate({ url: 'https://www.producthunt.com/posts/rick' });
    await sleep(7000);
    
    const result = await Runtime.evaluate({
      expression: `
        (function() {
          // Find the main upvote button at the top
          // It's typically a prominent button showing the total vote count
          const allButtons = [...document.querySelectorAll('button, a')];
          
          // Find vote count in the hero area
          const heroArea = document.querySelector('[class*="hero"], [class*="product-header"], [class*="post-header"], main > div:first-child');
          
          // Look at page structure
          const mainEl = document.querySelector('main');
          const firstSection = mainEl?.querySelector('section, div[class*="container"]');
          
          // Get all text containing numbers that could be vote count
          const votesText = [...document.querySelectorAll('*')].filter(el => {
            if (el.children.length > 0) return false;
            const t = el.textContent.trim();
            return /^\\d+$/.test(t) && parseInt(t) >= 10;
          }).map(el => ({
            text: el.textContent.trim(),
            tag: el.tagName,
            class: el.className?.substring(0, 100),
            parentClass: el.parentElement?.className?.substring(0, 100),
            grandParentClass: el.parentElement?.parentElement?.className?.substring(0, 100)
          }));
          
          // Try to get vote from data
          const scripts = [...document.querySelectorAll('script[type="application/json"], script:not([src])')];
          let voteData = null;
          scripts.forEach(s => {
            const text = s.textContent;
            const m = text.match(/"votesCount":(\d+)/);
            if (m && !voteData) voteData = { votesCount: parseInt(m[1]), context: text.substring(Math.max(0, m.index-50), m.index+100) };
          });
          
          return {
            title: document.title,
            url: window.location.href,
            votesText: votesText.slice(0, 15),
            voteData,
            // The specific upvote button with count
            upvoteButtons: [...document.querySelectorAll('button')].filter(b => 
              b.textContent.includes('Upvoted') || b.textContent.match(/\\d+ points/)
            ).map(b => ({ text: b.textContent.trim().substring(0, 100), class: b.className?.substring(0, 150) }))
          };
        })()
      `,
      returnByValue: true
    });
    
    const d = result.result.value;
    console.log('Title:', d.title);
    console.log('URL:', d.url);
    console.log('Upvote buttons:', JSON.stringify(d.upvoteButtons));
    console.log('Vote data from scripts:', JSON.stringify(d.voteData));
    console.log('Numbers >= 10:', JSON.stringify(d.votesText));
    
    // Also fetch via PH API with cookies
    const apiResult = await Runtime.evaluate({
      expression: `
        fetch('https://www.producthunt.com/frontend/graphql', {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json',
            'Accept': 'application/json'
          },
          credentials: 'include',
          body: JSON.stringify({
            operationName: "PostPageQuery",
            variables: { slug: "rick" },
            query: "query { post(slug: \\"rick\\") { id name votesCount commentsCount dailyRank } }"
          })
        }).then(r => r.text()).then(t => t.substring(0, 2000))
      `,
      returnByValue: false,
      awaitPromise: true
    });
    console.log('API fetch result:', apiResult.result?.value || 'none');
    
  } catch(e) {
    console.error('Error:', e.message, e.stack);
  } finally {
    if (client) await client.close();
  }
}

run();
