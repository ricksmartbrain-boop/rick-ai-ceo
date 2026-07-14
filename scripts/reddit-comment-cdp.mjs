#!/usr/bin/env node
// Post a comment on a Reddit thread via CDP (old.reddit.com)
// Usage: node reddit-comment-cdp.mjs <thread_url> <comment_text>

import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const WS = require('/Users/rickthebot/.openclaw/workspace/node_modules/ws');

const PORT = 9223;
const DELAY = ms => new Promise(r => setTimeout(r, ms));

async function getPages() {
  const http = require('http');
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
  if (result?.result?.exceptionDetails) {
    return { error: result.result.exceptionDetails.text || 'eval error' };
  }
  return result?.result?.result?.value ?? result?.result?.result;
}

async function getAllCookies(ws) {
  const result = await sendCmd(ws, 'Network.getAllCookies');
  return result?.result?.cookies ?? [];
}

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

async function postComment(threadUrl, commentText) {
  // Convert to old.reddit.com for simpler DOM
  const oldUrl = threadUrl.replace('www.reddit.com', 'old.reddit.com');
  
  const pages = await getPages();
  // Find or use any Reddit tab
  let tab = pages.find(p => p.type === 'page' && p.url.includes('old.reddit.com'));
  if (!tab) tab = pages.find(p => p.type === 'page' && p.url.includes('reddit.com') && !p.url.includes('submit'));
  if (!tab) {
    console.log('ERROR: No Reddit tab found in CDP');
    return false;
  }
  
  console.log(`Connecting to tab: ${tab.url.substring(0, 80)}`);
  const ws = await connectToPage(tab.webSocketDebuggerUrl);
  
  // Navigate to thread
  console.log(`Navigating to: ${oldUrl}`);
  await sendCmd(ws, 'Page.navigate', { url: oldUrl });
  await DELAY(5000);

  // Check if logged in using the real auth cookie, not the login link.
  const cookies = await getAllCookies(ws);
  const authCookie = cookies.find(c => c.name === 'reddit_session');
  const loggedIn = !!authCookie;
  console.log(`Logged in: ${loggedIn}`);
  if (!loggedIn) {
    console.log('ERROR: Not logged into Reddit');
    ws.close();
    return false;
  }

  // Get username
  const username = await evalExpr(ws, `
    (() => {
      const value = (document.querySelector('span.user-name') || document.querySelector('.user a'))?.textContent?.trim() || 'unknown';
      return value === 'Log in' ? 'unknown' : value;
    })()
  `);
  console.log(`Username: ${username}`);
  
  // Find the comment textarea (old reddit)
  const hasCommentBox = await evalExpr(ws, `
    !!document.querySelector('.commentarea textarea[name="text"], .usertext-edit textarea')
  `);
  console.log(`Comment box found: ${hasCommentBox}`);
  
  if (!hasCommentBox) {
    console.log('ERROR: No comment box found. May need to expand it.');
    // Try clicking the comment box area
    const expanded = await evalExpr(ws, `
      (function() {
        // Try the "commenting as" area or reply button
        const replyBtn = document.querySelector('.commentarea .usertext-edit');
        if (replyBtn) return 'already_visible';
        
        // Old reddit might have a collapsed comment area
        const commentForm = document.querySelector('form.cloneable');
        if (commentForm) return 'form_found';
        
        return 'not_found';
      })()
    `);
    console.log(`Expand attempt: ${expanded}`);
    ws.close();
    return false;
  }
  
  // Type the comment
  const escaped = commentText.replace(/\\/g, '\\\\').replace(/`/g, '\\`').replace(/\$/g, '\\$');
  const typed = await evalExpr(ws, `
    (function() {
      const ta = document.querySelector('.commentarea textarea[name="text"], .usertext-edit textarea');
      if (!ta) return 'no_textarea';
      ta.focus();
      ta.value = \`${escaped}\`;
      ta.dispatchEvent(new Event('input', {bubbles: true}));
      ta.dispatchEvent(new Event('change', {bubbles: true}));
      return 'typed';
    })()
  `);
  console.log(`Typed: ${typed}`);
  
  if (typed !== 'typed') {
    ws.close();
    return false;
  }
  
  await DELAY(1000);
  
  // Click submit
  const submitted = await evalExpr(ws, `
    (function() {
      const btn = document.querySelector('.commentarea button[type="submit"], .commentarea .save-button button, .usertext-buttons button.save');
      if (!btn) {
        // Try finding by text
        const buttons = [...document.querySelectorAll('.commentarea button')];
        const saveBtn = buttons.find(b => b.textContent.trim().toLowerCase() === 'save' || b.textContent.trim().toLowerCase() === 'comment');
        if (saveBtn) { saveBtn.click(); return 'clicked_by_text'; }
        return 'no_submit_button';
      }
      btn.click();
      return 'clicked';
    })()
  `);
  console.log(`Submit: ${submitted}`);
  
  await DELAY(3000);
  
  // Check for errors
  const result = await evalExpr(ws, `
    (function() {
      const err = document.querySelector('.error, .status-msg.error, .ratelimit');
      if (err && err.textContent.trim()) return 'error: ' + err.textContent.trim();
      // Check if comment appeared
      const comments = document.querySelectorAll('.comment');
      return 'comment_count: ' + comments.length;
    })()
  `);
  console.log(`Result: ${result}`);
  
  ws.close();
  return submitted.startsWith('clicked');
}

const [,, threadUrl, ...commentParts] = process.argv;
const commentText = commentParts.join(' ');

if (!threadUrl || !commentText) {
  console.log('Usage: node reddit-comment-cdp.mjs <thread_url> "comment text"');
  process.exit(1);
}

postComment(threadUrl, commentText)
  .then(ok => { 
    console.log(ok ? 'COMMENT_POSTED' : 'COMMENT_FAILED'); 
    process.exit(ok ? 0 : 1); 
  })
  .catch(e => { console.error('ERROR:', e.message); process.exit(1); });
