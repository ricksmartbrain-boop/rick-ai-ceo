#!/usr/bin/env node
/**
 * PH Comment Monitor + Reply via CDP port 9223
 */

const CDP = require('chrome-remote-interface');
const fs = require('fs');

const PH_POST_URL = 'https://www.producthunt.com/posts/rick';
const RICK_HANDLE = 'meetrickai';
const CDP_PORT = 9223;

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function navigateAndWait(Page, Runtime, url) {
  await Page.navigate({ url });
  await Page.loadEventFired();
  await sleep(5000);
  // Scroll to trigger lazy loads
  await Runtime.evaluate({ expression: 'window.scrollTo(0, 500)' });
  await sleep(1000);
  await Runtime.evaluate({ expression: 'window.scrollTo(0, document.body.scrollHeight)' });
  await sleep(2000);
  await Runtime.evaluate({ expression: 'window.scrollTo(0, 0)' });
  await sleep(1000);
}

async function run() {
  let client;
  try {
    const targets = await CDP.List({ port: CDP_PORT });
    // Find PH tab
    let target = targets.find(t => t.url && t.url.includes('producthunt.com'));
    if (!target) {
      target = targets.find(t => t.type === 'page');
    }
    console.log('Using target:', target.url);

    client = await CDP({ port: CDP_PORT, target: target.id });
    const { Page, Runtime, DOM, Network, Input } = client;

    await Network.enable();
    await Page.enable();
    await DOM.enable();

    console.log('Navigating to PH post page...');
    await Page.navigate({ url: PH_POST_URL });
    await Page.loadEventFired();
    await sleep(6000);

    // Scroll to load all comments
    for (let i = 0; i < 5; i++) {
      await Runtime.evaluate({ expression: `window.scrollTo(0, document.body.scrollHeight)` });
      await sleep(1500);
    }
    await Runtime.evaluate({ expression: `window.scrollTo(0, 0)` });
    await sleep(1000);

    // Extract page data: votes + comments
    const pageData = await Runtime.evaluate({
      expression: `
        (function() {
          const result = {
            title: document.title,
            url: window.location.href,
            voteCount: null,
            comments: [],
            loginStatus: null,
            rawCommentCount: 0
          };

          // Check login status - look for user menu or avatar
          const loginIndicators = document.querySelectorAll('[data-test="user-menu"], [href*="/meetrickai"], [aria-label*="profile" i], nav [class*="avatar" i]');
          result.loginStatus = loginIndicators.length > 0 ? 'logged-in indicators found: ' + loginIndicators.length : 'no login indicators';

          // Get vote count
          const bodyText = document.body.innerText;
          
          // Try structured vote selectors
          const voteSelectors = [
            '[data-test="vote-button"]',
            '[class*="voteCount"]',
            '[class*="vote-count"]',
            '[class*="upvote"]'
          ];
          for (const sel of voteSelectors) {
            const el = document.querySelector(sel);
            if (el) {
              const num = el.textContent?.trim();
              if (num && /^\\d+$/.test(num)) {
                result.voteCount = parseInt(num);
                result.voteSelector = sel;
                break;
              }
            }
          }

          // Try to get vote from page text patterns
          const votePatterns = [
            /(\\d+)\\s*upvotes?/i,
            /upvotes?\\s*\\n?\\s*(\\d+)/i
          ];
          for (const pat of votePatterns) {
            const m = bodyText.match(pat);
            if (m) { result.voteFromText = parseInt(m[1]); break; }
          }

          // Extract comments using multiple strategies
          const commentContainers = [];
          
          // Strategy 1: data-sentry-component
          document.querySelectorAll('[data-sentry-component*="Comment"]').forEach(el => {
            if (!el.closest('[data-sentry-component*="Comment"]')?.parentElement?.closest('[data-sentry-component*="Comment"]')) {
              commentContainers.push(el);
            }
          });
          
          // Strategy 2: generic comment selectors
          if (commentContainers.length === 0) {
            document.querySelectorAll('[data-test="comment"], [class*="commentItem"], [class*="comment-item"]').forEach(el => {
              commentContainers.push(el);
            });
          }

          result.rawCommentCount = commentContainers.length;

          commentContainers.forEach((el, idx) => {
            const commentData = { idx, author: null, authorHref: null, text: null, isRickAuthor: false };
            
            // Author
            const authorLinks = el.querySelectorAll('a[href*="/@"]');
            authorLinks.forEach(link => {
              const match = link.href?.match(\\/@([^/?]+)/);
              if (match && !commentData.author) {
                commentData.author = match[1];
                commentData.authorHref = link.href;
                if (match[1].toLowerCase() === 'meetrickai') commentData.isRickAuthor = true;
              }
            });

            // Text
            const pEls = el.querySelectorAll('p');
            if (pEls.length > 0) {
              commentData.text = Array.from(pEls).map(p => p.textContent?.trim()).filter(Boolean).join(' ');
            } else {
              const textEl = el.querySelector('[class*="body"], [class*="content"], [class*="text"]');
              commentData.text = textEl?.textContent?.trim();
            }

            if (commentData.author || commentData.text) {
              result.comments.push(commentData);
            }
          });

          // Fallback: dump some page structure for debugging
          result.pageStructureSample = document.body.innerText.substring(0, 2000);

          return result;
        })()
      `,
      returnByValue: true
    });

    const data = pageData.result.value;
    console.log('\n=== PAGE DATA ===');
    console.log('Title:', data.title);
    console.log('Vote count (selector):', data.voteCount);
    console.log('Vote count (text):', data.voteFromText);
    console.log('Login status:', data.loginStatus);
    console.log('Raw comment containers found:', data.rawCommentCount);
    console.log('Parsed comments:', data.comments.length);
    
    if (data.comments.length > 0) {
      console.log('\n=== COMMENTS ===');
      data.comments.forEach((c, i) => {
        console.log(`\n[${i}] @${c.author || 'unknown'} (rick: ${c.isRickAuthor})`);
        console.log(`    ${(c.text || '').substring(0, 200)}`);
      });
    } else {
      console.log('\n=== PAGE TEXT SAMPLE ===');
      console.log(data.pageStructureSample?.substring(0, 2000));
    }

    // Check PH homepage for ranking
    console.log('\n\nChecking PH homepage for ranking...');
    await Page.navigate({ url: 'https://www.producthunt.com' });
    await Page.loadEventFired();
    await sleep(4000);

    const rankingData = await Runtime.evaluate({
      expression: `
        (function() {
          const bodyText = document.body.innerText;
          // Look for Rick in the page
          const rickIdx = bodyText.toLowerCase().indexOf('rick');
          if (rickIdx >= 0) {
            return {
              found: true,
              context: bodyText.substring(Math.max(0, rickIdx - 100), rickIdx + 300)
            };
          }
          // Get product list
          const products = [];
          document.querySelectorAll('[class*="item"], [data-test*="product"]').forEach((el, i) => {
            const text = el.textContent?.trim()?.substring(0, 100);
            if (text && i < 20) products.push({ i, text });
          });
          return { found: false, products: products.slice(0, 10), pageTitle: document.title };
        })()
      `,
      returnByValue: true
    });

    console.log('Ranking data:', JSON.stringify(rankingData.result.value, null, 2));

    // Save all data
    fs.writeFileSync('/tmp/ph-full-data.json', JSON.stringify({
      pageData: data,
      rankingData: rankingData.result.value,
      timestamp: new Date().toISOString()
    }, null, 2));
    console.log('\nFull data saved to /tmp/ph-full-data.json');

  } catch (err) {
    console.error('Error:', err.message, err.stack);
    process.exit(1);
  } finally {
    if (client) await client.close();
  }
}

run();
