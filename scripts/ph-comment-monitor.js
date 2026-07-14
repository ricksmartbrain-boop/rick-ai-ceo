#!/usr/bin/env node
/**
 * Product Hunt comment monitor + action planner.
 *
 * Read-only by design.
 * Parses the visible PH thread, identifies duplicate maker comments,
 * maps external comments to nearby maker replies, and drafts cleaner replacements.
 */

const CANDIDATE_CDP_PORTS = [9229, 9225, 9223, 9222];
const PH_POST_URL = 'https://www.producthunt.com/posts/rick';
const MAKER_NAMES = ['rick', 'meetrickai'];
const KNOWN_AUTHORS = ['Rick 🤖', 'Paula Nwadiaro', 'Vladyslav Podoliako'];

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function getPageList(port) {
  const res = await fetch(`http://localhost:${port}/json`);
  return res.json();
}

async function openNewTab(port) {
  const res = await fetch(`http://localhost:${port}/json/new`);
  return res.json();
}

async function findWorkingPort() {
  for (const port of CANDIDATE_CDP_PORTS) {
    try {
      const pages = await getPageList(port);
      if (Array.isArray(pages)) return { port, pages };
    } catch {}
  }
  throw new Error(`Cannot connect to CDP Chrome on ports ${CANDIDATE_CDP_PORTS.join(', ')}`);
}

async function connectCDP(wsDebuggerUrl) {
  const WebSocket = (await import('ws')).default;
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsDebuggerUrl);
    let cmdId = 1;
    const pending = new Map();
    const listeners = new Map();

    ws.on('open', () => resolve({ ws, send, on: onEvent }));
    ws.on('error', reject);
    ws.on('message', raw => {
      const msg = JSON.parse(raw);
      if (msg.id && pending.has(msg.id)) {
        const pair = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) pair.reject(new Error(msg.error.message));
        else pair.resolve(msg.result);
      }
      if (msg.method) {
        const cbs = listeners.get(msg.method) || [];
        cbs.forEach(cb => cb(msg.params));
      }
    });

    function send(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = cmdId++;
        pending.set(id, { resolve, reject });
        ws.send(JSON.stringify({ id, method, params }));
      });
    }

    function onEvent(method, cb) {
      if (!listeners.has(method)) listeners.set(method, []);
      listeners.get(method).push(cb);
    }
  });
}

async function evaluate(cdp, fn, ...args) {
  const expr = args.length
    ? `(${fn.toString()})(${args.map(a => JSON.stringify(a)).join(',')})`
    : `(${fn.toString()})()`;
  const result = await cdp.send('Runtime.evaluate', {
    expression: expr,
    returnByValue: true,
    awaitPromise: true,
  });
  return result.result?.value;
}

async function navigate(cdp, url) {
  await cdp.send('Page.navigate', { url });
  await new Promise(resolve => {
    cdp.on('Page.loadEventFired', resolve);
    setTimeout(resolve, 10000);
  });
  await sleep(3000);
}

function extractSnapshot() {
  const lines = (document.body.innerText || '')
    .split('\n')
    .map(s => s.replace(/\s+/g, ' ').trim())
    .filter(Boolean);

  return {
    url: location.href,
    title: document.title,
    bodyPreview: (document.body.innerText || '').slice(0, 5000),
    lines,
  };
}

function normalize(text) {
  return (text || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function short(text, n = 220) {
  return text && text.length > n ? `${text.slice(0, n - 1)}…` : text;
}

function isMakerName(value) {
  const v = (value || '').toLowerCase();
  return MAKER_NAMES.some(name => v.includes(name));
}

function shouldSkipLine(line) {
  return [
    'Upvote', 'Upvoted', 'Reply', 'Report', 'Share', 'Award',
    'Maker', 'Folderly', 'Comment', 'Reviews', 'Most Informative'
  ].includes(line) || /^\(\d+\)$/.test(line) || /^\d+[hd] ago$/.test(line) || /^\d+d ago$/.test(line);
}

function buildReply(commentText) {
  const t = (commentText || '').toLowerCase();

  if (t.includes('different business') || t.includes('business types') || t.includes('saas') || t.includes('agencies') || t.includes('creators')) {
    return 'Good question. The core loop stays the same, but the operating context changes. SaaS leans into support, retention, and revenue ops. Agencies lean into pipeline, delivery, and follow-up. Creators lean into publishing cadence, audience feedback, and monetization.';
  }
  if (t.includes('found out') || t.includes('not from you') || t.includes('from ph feed')) {
    return 'Fair 😅 that one is on me. We shipped the launch fast and I skipped the very important step of telling my own founder first.';
  }
  if (t.includes('how') && (t.includes('work') || t.includes('setup') || t.includes('set up'))) {
    return 'It behaves more like an operator than a chatbot. You connect the stack, give it a standing mandate, and it monitors, executes, and reports instead of waiting for prompts.';
  }
  if (t.includes('price') || t.includes('pricing') || t.includes('cost')) {
    return 'We kept pricing simple for early users. The goal is to let people test the workflow first, then pay once the always-on operating layer is actually useful.';
  }
  if (t.includes('congrats') || t.includes('congratulations') || t.includes('great') || t.includes('love this')) {
    return 'Thank you, appreciate that. We are still tightening the product in public, so reactions like this genuinely help.';
  }
  return 'Appreciate you jumping in. We are still tightening the product in public, so thoughtful comments like this are genuinely useful.';
}

function parseRows(lines) {
  const rows = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (!KNOWN_AUTHORS.includes(line)) {
      i += 1;
      continue;
    }

    const author = line;
    i += 1;
    const chunk = [];
    while (i < lines.length && !KNOWN_AUTHORS.includes(lines[i])) {
      if (!shouldSkipLine(lines[i])) chunk.push(lines[i]);
      i += 1;
    }

    const text = chunk.join(' ').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    rows.push({ author, text, isMaker: isMakerName(author) });
  }

  return rows;
}

function analyze(snapshot) {
  const rows = parseRows(snapshot.lines);
  const makerReplies = [];
  const externalComments = [];
  const replyFingerprints = new Map();
  const actionItems = [];

  rows.forEach((row, idx) => {
    const item = {
      index: idx,
      author: row.author,
      isMaker: row.isMaker,
      text: short(row.text),
      rawText: row.text,
    };

    if (item.isMaker) {
      const key = normalize(row.text);
      const prev = replyFingerprints.get(key);
      item.duplicateOf = prev ?? null;
      if (!replyFingerprints.has(key)) replyFingerprints.set(key, idx);
      makerReplies.push(item);
      if (item.duplicateOf !== null) {
        actionItems.push({
          type: 'duplicate-maker-reply',
          replyIndex: idx,
          duplicateOf: item.duplicateOf,
          text: item.text,
          recommendation: 'Delete or collapse this duplicate reply. Keep only one version in-thread.',
        });
      }
      return;
    }

    const nextMaker = rows.slice(idx + 1).find(r => r.isMaker);
    const previousMaker = [...rows.slice(0, idx)].reverse().find(r => r.isMaker);
    const suggestedReply = buildReply(row.text);
    const matchedReply = nextMaker && normalize(nextMaker.text) !== normalize(previousMaker?.text || '') ? nextMaker.text : null;

    const ext = {
      index: idx,
      author: row.author,
      isMaker: false,
      text: short(row.text),
      rawText: row.text,
      suggestedReply,
      matchedMakerReply: matchedReply ? short(matchedReply) : null,
    };
    externalComments.push(ext);

    if (!matchedReply) {
      actionItems.push({
        type: 'needs-reply',
        commentIndex: idx,
        author: row.author,
        comment: ext.text,
        recommendation: suggestedReply,
      });
    }
  });

  return {
    page: {
      url: snapshot.url,
      title: snapshot.title,
    },
    summary: {
      totalBlocks: rows.length,
      makerReplies: makerReplies.length,
      externalComments: externalComments.length,
      duplicateMakerReplies: actionItems.filter(x => x.type === 'duplicate-maker-reply').length,
      unresolvedComments: actionItems.filter(x => x.type === 'needs-reply').length,
    },
    actionItems,
    externalComments,
    makerReplies,
    preview: snapshot.bodyPreview,
  };
}

async function main() {
  console.log('PH monitor mode, read-only');

  let port;
  let pages;
  try {
    const found = await findWorkingPort();
    port = found.port;
    pages = found.pages;
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }

  let targetPage = pages.find(p => p.type === 'page' && !p.url.startsWith('chrome-extension'));
  if (!targetPage) {
    targetPage = await openNewTab(port);
    await sleep(1000);
    pages = await getPageList(port);
    targetPage = pages.find(p => p.type === 'page');
  }

  if (!targetPage?.webSocketDebuggerUrl) {
    console.error('No usable page found');
    process.exit(1);
  }

  console.log(`Using CDP port ${port}`);

  const cdp = await connectCDP(targetPage.webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await navigate(cdp, PH_POST_URL);

  const snapshot = await evaluate(cdp, extractSnapshot);
  const report = analyze(snapshot);
  console.log(JSON.stringify(report, null, 2));
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
