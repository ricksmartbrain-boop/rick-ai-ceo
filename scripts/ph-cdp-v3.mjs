#!/usr/bin/env node
// PH full comment scrape v3
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const WebSocket = require('/Users/rickthebot/.openclaw/workspace/node_modules/ws');

const PORT = 9229;
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
        reject(new Error(`Timeout for ${method}`));
      }
    }, 45000);
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
    awaitPromise: true
  });
  return result?.result?.result?.value;
}

async function connectToPage(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    ws.on('open', resolve);
    ws.on('error', reject);
  });
  ws.on('message', (data) => {
    try {
      const msg = JSON.parse(data.toString());
      if (msg.id && pending.has(msg.id)) {
        const { resolve } = pending.get(msg.id);
        pending.delete(msg.id);
        resolve(msg);
      }
    } catch(e) {}
  });
  return ws;
}

async function run() {
  const pages = await getPages();
  const postsPage = pages.find(p => p.url.includes('producthunt.com') && p.title.includes('Rick'));
  
  if (!postsPage) { console.error('No PH page'); process.exit(1); }
  
  console.log('Using:', postsPage.url);
  const ws = await connectToPage(postsPage.webSocketDebuggerUrl);
  await sendCmd(ws, 'Page.enable');
  await sendCmd(ws, 'Runtime.enable');

  // Get full body text
  const fullBody = await evalExpr(ws, 'document.body.innerText');
  console.log('FULL_BODY_LENGTH:', fullBody?.length);
  console.log('\n=== FULL PAGE TEXT ===');
  console.log(fullBody);

  // Get vote count specifically
  const voteCount = await evalExpr(ws, `
    (() => {
      // "Upvoted • 12 points" pattern
      const text = document.body.innerText;
      const m = text.match(/Upvoted[^\\n]*?(\\d+)\\s+point/i) || text.match(/(\\d+)\\s+point/i);
      return m ? m[1] : 'not found';
    })()
  `);
  console.log('\nVOTE COUNT:', voteCount);

  ws.close();
}

run().catch(e => { console.error(e.message); process.exit(1); });
