#!/usr/bin/env node
// PH comment monitor + reply via CDP WebSocket
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const WebSocket = require('/Users/rickthebot/.openclaw/workspace/node_modules/ws');

const PORT = 9229;
const PAGE_ID = 'B30771CEB09671FE83FAF924868D434D';
const WS_URL = `ws://localhost:${PORT}/devtools/page/${PAGE_ID}`;
const DELAY = ms => new Promise(r => setTimeout(r, ms));

let msgId = 1;
const pending = new Map();

function sendCmd(ws, method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = msgId++;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`Timeout for ${method}`));
      }
    }, 30000);
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

async function run() {
  const ws = new WebSocket(WS_URL);

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

  await sendCmd(ws, 'Page.enable');
  await sendCmd(ws, 'Runtime.enable');

  console.log('Connected to CDP Chrome tab');
  console.log('Navigating to https://www.producthunt.com/posts/rick ...');

  await sendCmd(ws, 'Page.navigate', { url: 'https://www.producthunt.com/posts/rick' });
  await DELAY(6000);

  // Scroll to load all comments
  await evalExpr(ws, 'window.scrollTo(0, document.body.scrollHeight)');
  await DELAY(2000);
  await evalExpr(ws, 'window.scrollTo(0, document.body.scrollHeight)');
  await DELAY(2000);

  const title = await evalExpr(ws, 'document.title');
  console.log('Page:', title);

  // Get vote count + all comments from Apollo state
  const dataJson = await evalExpr(ws, `
    (() => {
      const apolloState = window.__APOLLO_STATE__;
      if (!apolloState) return JSON.stringify({ error: 'no apollo state' });

      // Get post data
      const postKeys = Object.keys(apolloState).filter(k => k.startsWith('Post:'));
      const posts = postKeys.map(k => {
        const p = apolloState[k];
        return { id: k, name: p?.name, slug: p?.slug, votesCount: p?.votesCount, commentsCount: p?.commentsCount };
      }).filter(p => p.name && p.slug?.includes('rick'));

      // Build user map
      const userMap = {};
      Object.keys(apolloState).filter(k => k.startsWith('User:')).forEach(k => {
        const u = apolloState[k];
        if (u?.username) userMap[k] = { username: u.username, name: u.name };
      });

      // Get all comments
      const commentKeys = Object.keys(apolloState).filter(k => k.startsWith('Comment:'));
      const comments = commentKeys.map(k => {
        const c = apolloState[k];
        if (!c || !c.body) return null;
        const userRef = c.user?.__ref;
        const userInfo = userRef ? userMap[userRef] : null;
        const parentRef = c.parent?.__ref || null;
        return {
          id: k.replace('Comment:', ''),
          body: c.body,
          username: userInfo?.username || 'unknown',
          name: userInfo?.name || '',
          isHunterOrMaker: !!c.isHunterOrMaker,
          parentId: parentRef ? parentRef.replace('Comment:', '') : null,
          createdAt: c.createdAt || ''
        };
      }).filter(Boolean);

      return JSON.stringify({ posts, comments });
    })()
  `);

  let data = {};
  try { data = JSON.parse(dataJson); } catch(e) { console.error('Parse error:', e.message, dataJson?.substring(0, 200)); }

  const rickPost = (data.posts || []).find(p => p.slug?.includes('rick')) || data.posts?.[0];
  const allComments = data.comments || [];

  console.log('\n=== RICK ON PH ===');
  if (rickPost) {
    console.log('Upvotes:', rickPost.votesCount);
    console.log('Comments:', rickPost.commentsCount);
    console.log('Post:', rickPost.name, '| Slug:', rickPost.slug);
  } else {
    console.log('Post data not found. Posts found:', JSON.stringify(data.posts));
  }

  console.log('\n=== ALL COMMENTS (' + allComments.length + ') ===');
  allComments.forEach((c, i) => {
    const marker = c.isHunterOrMaker ? ' [MAKER]' : '';
    const replyMark = c.parentId ? ` [REPLY to ${c.parentId}]` : '';
    console.log(`[${i+1}] @${c.username}${marker}${replyMark} | ID:${c.id}`);
    console.log(`  "${c.body.substring(0, 200)}"`);
  });

  // Identify top-level comments that don't have a maker reply
  const makerRepliedToIds = new Set(
    allComments
      .filter(c => c.isHunterOrMaker && c.parentId)
      .map(c => c.parentId)
  );

  const topLevelComments = allComments.filter(c => !c.parentId && !c.isHunterOrMaker);
  const needsReply = topLevelComments.filter(c => !makerRepliedToIds.has(c.id));

  console.log('\n=== NEEDS REPLY (' + needsReply.length + ') ===');
  needsReply.forEach((c, i) => {
    console.log(`[${i+1}] @${c.username} (ID:${c.id}): "${c.body.substring(0, 150)}"`);
  });

  ws.close();
  
  // Output JSON for next step
  console.log('\n__JSON_OUTPUT__');
  console.log(JSON.stringify({
    upvotes: rickPost?.votesCount,
    commentsCount: rickPost?.commentsCount,
    totalComments: allComments.length,
    makerRepliedToIds: [...makerRepliedToIds],
    needsReply,
    allComments
  }));
}

run().catch(e => {
  console.error('Fatal:', e.message);
  console.error(e.stack);
  process.exit(1);
});
