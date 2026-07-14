#!/usr/bin/env node
// Post video meme to Reddit via CDP
// Usage: node post-reddit-cdp.mjs <video_path> <title> <subreddit>

import { createRequire } from 'module';
import { readFileSync } from 'fs';
import path from 'path';
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
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`Timeout: ${method}`));
      }
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
    timeout: 10000
  });
  return result?.result?.result?.value ?? result?.result?.result;
}

async function navigate(ws, url, wait = 4000) {
  await sendCmd(ws, 'Page.navigate', { url });
  await DELAY(wait);
}

async function connectToPage(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WS(wsUrl);
    ws.on('message', (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.id && pending.has(msg.id)) {
        const { resolve } = pending.get(msg.id);
        pending.delete(msg.id);
        resolve(msg.result);
      }
    });
    ws.on('open', () => resolve(ws));
    ws.on('error', reject);
  });
}

async function postToReddit(videoPath, title, subreddit) {
  const pages = await getPages();
  const tab = pages.find(p => p.type === 'page' && p.url.includes('reddit.com'));
  
  if (!tab) {
    console.log('No Reddit tab found. Trying any page...');
    const anyTab = pages.find(p => p.type === 'page' && !p.url.includes('chrome://'));
    if (!anyTab) { console.log('No usable tab'); return false; }
  }

  const redditTab = pages.find(p => p.type === 'page' && p.url.includes('reddit.com')) 
    || pages.find(p => p.type === 'page' && !p.url.includes('chrome://'));
  
  console.log(`Using tab: ${redditTab.url}`);
  const ws = await connectToPage(redditTab.webSocketDebuggerUrl);

  // Navigate to Reddit submit page
  const submitUrl = `https://www.reddit.com/r/${subreddit}/submit`;
  console.log(`Navigating to ${submitUrl}...`);
  await navigate(ws, submitUrl, 5000);

  // Check login state
  const loginState = await evalExpr(ws, `
    document.querySelector('[data-testid="UserDropdown"], button[aria-label*="user"], a[href*="/user/"]') 
      ? 'logged_in' : 
      (document.querySelector('a[href*="/login"]') ? 'logged_out' : 'unknown')
  `);
  console.log(`Login: ${loginState}`);

  await DELAY(2000);

  // Click on Images & Video tab
  const mediaTabResult = await evalExpr(ws, `
    (function() {
      const buttons = [...document.querySelectorAll('button')];
      const imgVidBtn = buttons.find(b => 
        b.textContent.toLowerCase().includes('image') || 
        b.textContent.toLowerCase().includes('video') ||
        b.textContent.toLowerCase().includes('media')
      );
      if (imgVidBtn) { imgVidBtn.click(); return 'clicked: ' + imgVidBtn.textContent.trim(); }
      
      // Try tab buttons 
      const tabs = [...document.querySelectorAll('[role="tab"], .tab-link, nav a')];
      const t = tabs.find(t => t.textContent.toLowerCase().includes('image') || t.textContent.toLowerCase().includes('video'));
      if (t) { t.click(); return 'tab: ' + t.textContent.trim(); }
      
      return 'not_found';
    })()
  `);
  console.log(`Media tab: ${mediaTabResult}`);
  await DELAY(2000);

  // Intercept file chooser
  await sendCmd(ws, 'Page.setInterceptFileChooserDialog', { enabled: true });

  // Trigger file input
  const fileInputResult = await evalExpr(ws, `
    (function() {
      const inp = document.querySelector('input[type="file"]');
      if (inp) { inp.click(); return 'file_input'; }
      const uploadBtn = [...document.querySelectorAll('button')].find(b => 
        b.textContent.toLowerCase().includes('upload') ||
        b.textContent.toLowerCase().includes('choose file') ||
        b.textContent.toLowerCase().includes('add')
      );
      if (uploadBtn) { uploadBtn.click(); return 'upload_btn: ' + uploadBtn.textContent.trim(); }
      return 'not_found';
    })()
  `);
  console.log(`File input: ${fileInputResult}`);
  await DELAY(500);

  // Accept file chooser with video
  const absPath = path.resolve(videoPath);
  console.log(`Uploading: ${absPath}`);
  const chooserResult = await sendCmd(ws, 'Page.handleFileChooser', {
    action: 'accept',
    files: [absPath]
  });
  console.log(`Chooser: ${JSON.stringify(chooserResult)}`);
  await DELAY(8000); // Reddit processes video

  // Set title
  const titleResult = await evalExpr(ws, `
    (function() {
      const titleField = document.querySelector(
        'textarea[placeholder*="title" i], input[placeholder*="title" i], ' +
        '[data-testid="post-title-input"], textarea[name="title"]'
      );
      if (titleField) {
        titleField.focus();
        titleField.value = '';
        document.execCommand('selectAll');
        document.execCommand('insertText', false, ${JSON.stringify(title)});
        titleField.dispatchEvent(new Event('input', {bubbles:true}));
        titleField.dispatchEvent(new Event('change', {bubbles:true}));
        return 'title_set';
      }
      return 'no_title_field';
    })()
  `);
  console.log(`Title: ${titleResult}`);
  await DELAY(1000);

  // Check URL after posting
  const beforeUrl = await evalExpr(ws, 'window.location.href');
  
  // Submit
  const submitResult = await evalExpr(ws, `
    (function() {
      const btns = [...document.querySelectorAll('button')];
      const postBtn = btns.find(b => {
        const txt = b.textContent.trim().toLowerCase();
        return (txt === 'post' || txt === 'submit') && !b.disabled;
      });
      if (postBtn) { postBtn.click(); return 'submitted: ' + postBtn.textContent.trim(); }
      return 'submit_not_found';
    })()
  `);
  console.log(`Submit: ${submitResult}`);
  await DELAY(5000);

  const afterUrl = await evalExpr(ws, 'window.location.href');
  console.log(`Before: ${beforeUrl}`);
  console.log(`After: ${afterUrl}`);
  
  ws.close();
  return afterUrl !== beforeUrl;
}

const [,, videoPath, title, subreddit] = process.argv;
if (!videoPath || !title || !subreddit) {
  console.log('Usage: node post-reddit-cdp.mjs <video_path> <title> <subreddit>');
  process.exit(1);
}

postToReddit(videoPath, title, subreddit)
  .then(ok => { console.log(ok ? 'POST_SUCCESS' : 'POST_UNCERTAIN'); process.exit(0); })
  .catch(e => { console.error('ERROR:', e.message); process.exit(1); });
