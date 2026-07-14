#!/usr/bin/env node
const CDP = require('chrome-remote-interface');
const fs = require('fs');

const CDP_PORT = 9223;
const PH_POST_URL = 'https://www.producthunt.com/posts/rick';

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function run() {
  let client;
  try {
    const targets = await CDP.List({ port: CDP_PORT });
    const target = targets.find(t => t.url && t.url.includes('producthunt.com')) || targets.find(t => t.type === 'page');
    client = await CDP({ port: CDP_PORT, target: target.id });
    const { Page, Runtime, Network } = client;
    await Network.enable();
    await Page.enable();

    await Page.navigate({ url: PH_POST_URL });
    await sleep(8000);

    // Scroll multiple times to load all comments
    for (let i = 0; i < 6; i++) {
      await Runtime.evaluate({ expression: 'window.scrollTo(0, document.body.scrollHeight)' });
      await sleep(2000);
    }
    await sleep(2000);

    // Get full comments section HTML (larger)
    const r = await Runtime.evaluate({
      expression: `
        (function(){
          const section = document.querySelector('#comments');
          if (!section) return JSON.stringify({ error: 'no #comments' });
          return JSON.stringify({
            fullText: section.innerText,
            fullHtml: section.outerHTML.substring(0, 50000)
          });
        })()
      `,
      returnByValue: true
    });
    
    const data = JSON.parse(r.result.value);
    if (data.error) {
      console.log('Error:', data.error);
    } else {
      console.log('=== COMMENTS SECTION TEXT ===');
      console.log(data.fullText);
      fs.writeFileSync('/tmp/ph-comments-full.html', data.fullHtml || '');
      fs.writeFileSync('/tmp/ph-comments-text.txt', data.fullText || '');
    }

    // Also get upvote count from the post header area
    const voteR = await Runtime.evaluate({
      expression: `
        (function(){
          // Look for the vote button/count
          const body = document.body.innerText;
          const lines = body.split('\\n').filter(l => l.trim());
          return JSON.stringify({ 
            first50Lines: lines.slice(0, 50),
            voteArea: document.querySelector('[class*="vote"], [data-test*="vote"]')?.innerText
          });
        })()
      `,
      returnByValue: true
    });
    const voteData = JSON.parse(voteR.result.value);
    console.log('\n=== VOTE AREA ===', voteData.voteArea);
    console.log('\n=== FIRST 50 LINES ===');
    voteData.first50Lines.forEach((l, i) => console.log(i, l));

  } catch (err) {
    console.error('ERROR:', err.message);
    process.exit(1);
  } finally {
    if (client) await client.close();
  }
}

run();
