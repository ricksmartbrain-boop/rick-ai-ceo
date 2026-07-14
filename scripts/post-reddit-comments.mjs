#!/usr/bin/env node
// Post multiple Reddit comments via CDP (old.reddit.com)
// Reads comments from a JSON file and posts them sequentially

import { createRequire } from 'module';
import { readFileSync } from 'fs';
const require = createRequire(import.meta.url);
const WS = require('/Users/rickthebot/.openclaw/workspace/node_modules/ws');
const http = require('http');

const PORT = 9223;
const DELAY = ms => new Promise(r => setTimeout(r, ms));

function getPages() {
  return new Promise((resolve, reject) => {
    http.get(`http://localhost:${PORT}/json/list`, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => resolve(JSON.parse(data)));
    }).on('error', reject);
  });
}

let msgId = 1;
const pending = new Map();

function connectToPage(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WS(wsUrl);
    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.id && pending.has(msg.id)) {
        const { resolve } = pending.get(msg.id);
        pending.delete(msg.id);
        resolve(msg);
      }
    });
    ws.on('open', () => resolve(ws));
    ws.on('error', reject);
  });
}

function sendCmd(ws, method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = msgId++;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`Timeout: ${method}`));
    }, 30000);
    pending.set(id, {
      resolve: (v) => { clearTimeout(timer); resolve(v); },
      reject: (e) => { clearTimeout(timer); reject(e); }
    });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function evalExpr(ws, expression) {
  const result = await sendCmd(ws, 'Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
    timeout: 15000
  });
  return result?.result?.result?.value;
}

async function postOneComment(ws, threadUrl, commentText) {
  const oldUrl = threadUrl.replace('www.reddit.com', 'old.reddit.com');
  
  console.log(`\n--- Navigating to: ${oldUrl.substring(0, 80)}...`);
  await sendCmd(ws, 'Page.navigate', { url: oldUrl });
  await DELAY(5000);
  
  // Check logged in
  const user = await evalExpr(ws, `
    (document.querySelector('span.user-name') || document.querySelector('.user a'))?.textContent?.trim() || null
  `);
  if (!user) {
    console.log('ERROR: Not logged in');
    return false;
  }
  console.log(`Logged in as: ${user}`);
  
  // Check for comment box
  const hasBox = await evalExpr(ws, `!!document.querySelector('.commentarea textarea[name="text"]')`);
  if (!hasBox) {
    console.log('ERROR: No comment box found');
    return false;
  }
  
  // Use a safe approach: set value via JSON-encoded string injected by variable
  const safeComment = JSON.stringify(commentText);
  const typed = await evalExpr(ws, `
    (function() {
      const ta = document.querySelector('.commentarea textarea[name="text"]');
      if (!ta) return 'no_textarea';
      ta.focus();
      ta.value = ${safeComment};
      ta.dispatchEvent(new Event('input', {bubbles: true}));
      ta.dispatchEvent(new Event('change', {bubbles: true}));
      return 'typed_' + ta.value.length + '_chars';
    })()
  `);
  console.log(`Text: ${typed}`);
  
  if (!typed || !typed.startsWith('typed_')) {
    console.log('ERROR: Failed to type comment');
    return false;
  }
  
  await DELAY(1500);
  
  // Submit
  const submitted = await evalExpr(ws, `
    (function() {
      // Old reddit: .save-button button or button.save
      const btns = document.querySelectorAll('.commentarea .usertext-buttons button');
      for (const b of btns) {
        if (b.textContent.trim().toLowerCase() === 'save') {
          b.click();
          return 'clicked_save';
        }
      }
      // Fallback
      const btn = document.querySelector('.commentarea button[type="submit"]');
      if (btn) { btn.click(); return 'clicked_submit'; }
      return 'no_button_found';
    })()
  `);
  console.log(`Submit: ${submitted}`);
  
  if (!submitted.startsWith('clicked')) {
    return false;
  }
  
  await DELAY(4000);
  
  // Check for errors or rate limit
  const result = await evalExpr(ws, `
    (function() {
      const errs = document.querySelectorAll('.error, .status-msg');
      for (const e of errs) {
        const t = e.textContent.trim();
        if (t && t.length > 0 && !t.includes('commenting is locked')) return 'error: ' + t;
      }
      return 'ok';
    })()
  `);
  console.log(`Post-submit check: ${result}`);
  
  return result === 'ok' || !result.startsWith('error');
}

async function main() {
  const commentsFile = process.argv[2];
  if (!commentsFile) {
    console.log('Usage: node post-reddit-comments.mjs <comments.json>');
    console.log('JSON format: [{url: "...", comment: "..."}, ...]');
    process.exit(1);
  }
  
  const comments = JSON.parse(readFileSync(commentsFile, 'utf-8'));
  console.log(`Loaded ${comments.length} comments to post`);
  
  const pages = await getPages();
  let tab = pages.find(p => p.type === 'page' && p.url.includes('old.reddit.com'));
  if (!tab) tab = pages.find(p => p.type === 'page' && p.url.includes('reddit.com') && !p.url.includes('submit'));
  if (!tab) {
    console.log('ERROR: No Reddit tab in CDP');
    process.exit(1);
  }
  
  const ws = await connectToPage(tab.webSocketDebuggerUrl);
  
  let posted = 0;
  let failed = 0;
  
  for (let i = 0; i < comments.length; i++) {
    const { url, comment } = comments[i];
    console.log(`\n=== [${i+1}/${comments.length}] ===`);
    
    try {
      const ok = await postOneComment(ws, url, comment);
      if (ok) {
        posted++;
        console.log(`✅ Posted comment ${i+1}`);
      } else {
        failed++;
        console.log(`❌ Failed comment ${i+1}`);
      }
    } catch (e) {
      failed++;
      console.log(`❌ Error on comment ${i+1}: ${e.message}`);
    }
    
    // Wait between comments to avoid rate limiting
    if (i < comments.length - 1) {
      const waitSec = 120 + Math.floor(Math.random() * 60); // 2-3 min between
      console.log(`Waiting ${waitSec}s before next comment...`);
      await DELAY(waitSec * 1000);
    }
  }
  
  console.log(`\n=== DONE: ${posted} posted, ${failed} failed ===`);
  ws.close();
  process.exit(failed > 0 ? 1 : 0);
}

main().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
