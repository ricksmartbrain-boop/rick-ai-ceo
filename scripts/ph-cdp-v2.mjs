#!/usr/bin/env node
// PH comment monitor v2 - uses existing loaded tab + longer wait
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const WebSocket = require('/Users/rickthebot/.openclaw/workspace/node_modules/ws');

const PORT = 9229;
const DELAY = ms => new Promise(r => setTimeout(r, ms));

// Use the existing loaded PH posts tab - check all tabs first
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
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(msg.id);
        reject(new Error(`Timeout for ${method}`));
      }
    }, 45000);
  });
}

// Fix: use id not msg.id in timeout
function sendCmdFixed(ws, method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = msgId++;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
    const timer = setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`Timeout for ${method}`));
      }
    }, 45000);
    // Clear timer on resolve
    const orig = { resolve, reject };
    pending.set(id, {
      resolve: (v) => { clearTimeout(timer); orig.resolve(v); },
      reject: (e) => { clearTimeout(timer); orig.reject(e); }
    });
  });
}

async function evalExpr(ws, expression) {
  const result = await sendCmdFixed(ws, 'Runtime.evaluate', {
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
  console.log('Available pages:');
  pages.forEach(p => console.log(` - ${p.title} | ${p.url}`));

  // Find a page that's already on the posts page, or any PH page
  const postsPage = pages.find(p => p.url.includes('/posts/rick')) ||
                    pages.find(p => p.url.includes('producthunt.com'));

  if (!postsPage) {
    console.error('No PH page found!');
    process.exit(1);
  }

  console.log('\nUsing page:', postsPage.url);
  const ws = await connectToPage(postsPage.webSocketDebuggerUrl);

  await sendCmdFixed(ws, 'Page.enable');
  await sendCmdFixed(ws, 'Runtime.enable');

  // If not on posts page, navigate
  if (!postsPage.url.includes('/posts/rick')) {
    console.log('Navigating to /posts/rick...');
    await sendCmdFixed(ws, 'Page.navigate', { url: 'https://www.producthunt.com/posts/rick' });
    // Wait for load
    await new Promise((resolve) => {
      const handler = (data) => {
        try {
          const msg = JSON.parse(data.toString());
          if (msg.method === 'Page.loadEventFired') {
            ws.off('message', handler);
            resolve();
          }
        } catch(e) {}
      };
      ws.on('message', handler);
      setTimeout(resolve, 10000); // fallback
    });
  }

  await DELAY(5000);

  // Scroll to load all content
  await evalExpr(ws, 'window.scrollTo(0, document.body.scrollHeight)');
  await DELAY(2000);
  await evalExpr(ws, 'window.scrollTo(0, 0)');
  await DELAY(1000);
  await evalExpr(ws, 'window.scrollTo(0, document.body.scrollHeight)');
  await DELAY(3000);

  const title = await evalExpr(ws, 'document.title');
  const url = await evalExpr(ws, 'window.location.href');
  console.log('\nPage:', title);
  console.log('URL:', url);

  // Try Apollo state
  const apolloCheck = await evalExpr(ws, `
    (() => {
      const hasApollo = !!window.__APOLLO_STATE__;
      const keys = hasApollo ? Object.keys(window.__APOLLO_STATE__).length : 0;
      const sampleKeys = hasApollo ? Object.keys(window.__APOLLO_STATE__).slice(0, 10) : [];
      return JSON.stringify({ hasApollo, keys, sampleKeys });
    })()
  `);
  console.log('\nApollo check:', apolloCheck);

  // Try NextData
  const nextCheck = await evalExpr(ws, `
    (() => {
      const nd = window.__NEXT_DATA__;
      if (!nd) return 'no next data';
      return JSON.stringify({
        page: nd.page,
        propsKeys: Object.keys(nd.props || {}),
        pagePropsKeys: Object.keys(nd.props?.pageProps || {})
      });
    })()
  `);
  console.log('NextData check:', nextCheck);

  // Check actual DOM for comments
  const domCheck = await evalExpr(ws, `
    (() => {
      const body = document.body.innerText;
      // Count comment-like patterns
      const lines = body.split('\\n').filter(l => l.trim().length > 5);
      return JSON.stringify({
        totalLines: lines.length,
        bodyLength: body.length,
        preview: body.substring(0, 3000)
      });
    })()
  `);
  let domData = {};
  try { domData = JSON.parse(domCheck); } catch(e) {}
  console.log('\nDOM preview (first 3000 chars):');
  console.log(domData.preview || domCheck);

  // Try the PH GraphQL API directly
  console.log('\n=== Trying PH API approach ===');
  const apiResult = await evalExpr(ws, `
    (() => {
      // Look for any XHR/fetch data already cached
      const performance_entries = performance.getEntriesByType('resource')
        .filter(r => r.name.includes('graphql') || r.name.includes('api'))
        .map(r => r.name)
        .slice(0, 10);
      return JSON.stringify({ apiCalls: performance_entries });
    })()
  `);
  console.log('API calls detected:', apiResult);

  ws.close();
}

run().catch(e => {
  console.error('Fatal:', e.message);
  console.error(e.stack);
  process.exit(1);
});
