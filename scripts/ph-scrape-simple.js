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
    console.log('Target:', target.id, target.url);

    client = await CDP({ port: CDP_PORT, target: target.id });
    const { Page, Runtime, Network } = client;
    await Network.enable();
    await Page.enable();

    // Navigate
    console.log('Navigating...');
    await Page.navigate({ url: PH_POST_URL });
    
    // Wait for network idle
    await sleep(8000);
    
    // Scroll to load comments
    for (let i = 0; i < 4; i++) {
      await Runtime.evaluate({ expression: 'window.scrollTo(0, document.body.scrollHeight)' });
      await sleep(2000);
    }

    // Get the full page text
    const r1 = await Runtime.evaluate({
      expression: 'JSON.stringify({ title: document.title, url: window.location.href, text: document.body.innerText.substring(0, 8000) })',
      returnByValue: true
    });
    
    const pageInfo = JSON.parse(r1.result.value);
    console.log('Title:', pageInfo.title);
    console.log('URL:', pageInfo.url);
    console.log('\n=== PAGE TEXT ===\n', pageInfo.text);
    
    fs.writeFileSync('/tmp/ph-page-text.txt', pageInfo.text);
    
    // Also get HTML of comment section
    const r2 = await Runtime.evaluate({
      expression: `
        (function(){
          // Try to find comments area
          const selectors = ['[data-test="comments"]', '#comments', '[class*="Comments"]', '[class*="comments"]'];
          for (const s of selectors) {
            const el = document.querySelector(s);
            if (el) return JSON.stringify({ selector: s, html: el.outerHTML.substring(0, 10000) });
          }
          // Fallback: find any element with "comment" in class
          const allEls = document.querySelectorAll('*');
          for (const el of allEls) {
            if (el.className && typeof el.className === 'string' && el.className.toLowerCase().includes('comment') && el.children.length > 2) {
              return JSON.stringify({ selector: el.tagName + '.' + el.className.substring(0,50), html: el.outerHTML.substring(0, 10000) });
            }
          }
          return JSON.stringify({ selector: null, html: null });
        })()
      `,
      returnByValue: true
    });
    
    const commentSection = JSON.parse(r2.result.value);
    console.log('\n=== COMMENT SECTION ===');
    console.log('Selector:', commentSection.selector);
    if (commentSection.html) {
      console.log('HTML snippet:', commentSection.html.substring(0, 2000));
      fs.writeFileSync('/tmp/ph-comments-html.html', commentSection.html);
    }

  } catch (err) {
    console.error('ERROR:', err.message);
    console.error(err.stack);
    process.exit(1);
  } finally {
    if (client) await client.close();
  }
}

run();
