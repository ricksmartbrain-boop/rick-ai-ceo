const CDP = require('chrome-remote-interface');

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function run() {
  let client;
  try {
    client = await CDP({ port: 9222 });
    const { Page, Runtime, Network } = client;
    
    await Page.enable();
    await Runtime.enable();
    await Network.enable();
    
    // Intercept GraphQL for vote data
    const responses = [];
    Network.responseReceived(({ requestId, response }) => {
      if (response.url.includes('producthunt.com') && (response.url.includes('graphql') || response.url.includes('api'))) {
        responses.push({ requestId, url: response.url });
      }
    });
    
    console.log('Navigating to PH post page...');
    await Page.navigate({ url: 'https://www.producthunt.com/posts/rick' });
    await sleep(8000);
    
    // Get exact vote count and comment status
    const result = await Runtime.evaluate({
      expression: `
        (function() {
          const data = {};
          
          // Get the vote button count - main product vote
          // Look for the large vote button at the top of the post
          const voteButtons = [...document.querySelectorAll('button')].filter(b => {
            const text = b.textContent.trim();
            return /^\\d+$/.test(text) || text.toLowerCase().includes('upvote');
          });
          data.voteButtons = voteButtons.map(b => ({
            text: b.textContent.trim(),
            class: b.className?.substring(0, 150),
            ariaLabel: b.getAttribute('aria-label')
          }));
          
          // Get all thread IDs on page
          const threads = [...document.querySelectorAll('[data-test^="thread-"]')].map(t => {
            const testId = t.getAttribute('data-test');
            const threadId = testId?.replace('thread-', '');
            
            // Get all comments within this thread
            const comments = [...t.querySelectorAll('[data-test^="comment-"]')].map(c => {
              const cId = c.getAttribute('data-test')?.replace('comment-', '');
              const userLink = c.querySelector('a[href*="/@"]');
              const username = userLink?.getAttribute('href')?.replace('/@', '');
              const isMaker = !!c.querySelector('[class*="bg-success"]');
              const timeEl = c.querySelector('time, [class*="ago"]');
              const bodyEl = c.querySelector('[class*="prose"], p');
              return {
                id: cId,
                username,
                isMaker,
                time: timeEl?.textContent?.trim(),
                bodyPreview: bodyEl?.textContent?.trim().substring(0, 200)
              };
            });
            
            return { threadId, comments };
          });
          data.threads = threads;
          
          // Also get ranking info
          const rankingArea = document.querySelector('[data-test*="ranking"], [class*="rank"]');
          data.ranking = rankingArea ? rankingArea.textContent.trim() : null;
          
          // Page title for context
          data.title = document.title;
          data.url = window.location.href;
          
          return data;
        })()
      `,
      returnByValue: true
    });
    
    const d = result.result.value;
    console.log('URL:', d.url);
    console.log('Title:', d.title);
    console.log('\\nVote buttons found:');
    d.voteButtons?.forEach(b => console.log(' -', JSON.stringify(b)));
    
    console.log('\\nThreads:');
    d.threads?.forEach(t => {
      console.log('\\nThread', t.threadId + ':');
      t.comments?.forEach(c => {
        console.log(' Comment', c.id, '@' + c.username, c.isMaker ? '[MAKER]' : '', c.time || '');
        if (c.bodyPreview) console.log('   >', c.bodyPreview.substring(0, 150));
      });
    });
    
    // Also try the PH API directly
    console.log('\\n--- Fetching PH API for vote count ---');
    const apiResult = await Runtime.evaluate({
      expression: `
        fetch('https://www.producthunt.com/frontend/graphql', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            query: \`query { post(slug: "rick") { id name votesCount commentsCount dailyRank weeklyRank } }\`
          })
        }).then(r => r.json()).then(d => JSON.stringify(d))
      `,
      returnByValue: false,
      awaitPromise: true
    });
    console.log('API result:', apiResult.result?.value || JSON.stringify(apiResult.result));
    
  } catch(e) {
    console.error('Error:', e.message, e.stack);
  } finally {
    if (client) await client.close();
  }
}

run();
