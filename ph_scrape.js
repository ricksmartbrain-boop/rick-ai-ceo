const CDP = require('chrome-remote-interface');
const fs = require('fs');

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function run() {
  let client;
  try {
    client = await CDP({ port: 9222 });
    const { Page, Runtime } = client;
    
    await Page.enable();
    await Runtime.enable();
    
    console.log('Navigating to PH post...');
    await Page.navigate({ url: 'https://www.producthunt.com/posts/rick' });
    await sleep(8000);
    
    // Scroll to load all comments
    await Runtime.evaluate({ expression: 'window.scrollTo(0, document.body.scrollHeight)' });
    await sleep(3000);
    await Runtime.evaluate({ expression: 'window.scrollTo(0, 0)' });
    await sleep(2000);

    // Get page info
    const pageInfo = await Runtime.evaluate({
      expression: `
        (function() {
          const info = {};
          info.title = document.title;
          info.url = window.location.href;
          
          // All user profile links on page
          const profileLinks = [...document.querySelectorAll('a[href*="/@"]')].map(a => ({
            text: a.textContent.trim().substring(0, 50),
            href: a.getAttribute('href'),
            parentClass: a.parentElement?.className?.substring(0, 100),
            grandParentClass: a.parentElement?.parentElement?.className?.substring(0, 100)
          }));
          info.profileLinks = profileLinks;
          
          // Look for vote/upvote numbers
          const numberEls = [...document.querySelectorAll('*')].filter(el => {
            const t = el.textContent.trim();
            return el.children.length === 0 && /^\\d+$/.test(t) && parseInt(t) >= 1 && parseInt(t) < 10000;
          }).map(el => ({
            tag: el.tagName,
            class: el.className?.substring(0, 150),
            text: el.textContent.trim(),
            parentClass: el.parentElement?.className?.substring(0, 150),
            parentTag: el.parentElement?.tagName
          }));
          info.numbers = numberEls.slice(0, 30);
          
          // Check for comment inputs
          const commentInputs = [...document.querySelectorAll('textarea, [contenteditable="true"], [placeholder*="comment" i], [aria-label*="comment" i]')];
          info.commentInputs = commentInputs.map(el => ({
            tag: el.tagName,
            placeholder: el.getAttribute('placeholder'),
            ariaLabel: el.getAttribute('aria-label'),
            class: el.className?.substring(0, 150)
          }));
          
          return info;
        })()
      `,
      returnByValue: true
    });
    
    const data = pageInfo.result.value;
    console.log('Title:', data.title);
    console.log('URL:', data.url);
    console.log('Numbers:', JSON.stringify(data.numbers, null, 2));
    console.log('Profile links:', JSON.stringify(data.profileLinks?.slice(0, 30), null, 2));
    console.log('Comment inputs:', JSON.stringify(data.commentInputs, null, 2));
    
    // Save full HTML
    const htmlResult = await Runtime.evaluate({
      expression: 'document.body.innerHTML',
      returnByValue: true
    });
    fs.writeFileSync('/tmp/ph_page.html', htmlResult.result.value || '');
    console.log('Saved HTML to /tmp/ph_page.html (' + (htmlResult.result.value?.length || 0) + ' chars)');
    
  } catch(e) {
    console.error('Error:', e.message);
    console.error(e.stack);
  } finally {
    if (client) await client.close();
  }
}

run();
