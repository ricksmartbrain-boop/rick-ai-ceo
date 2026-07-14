#!/usr/bin/env node
/**
 * Product Hunt reply planner.
 *
 * Read-only by design.
 * Extracts visible comments, flags likely duplicates,
 * and drafts tighter, more specific replies for manual review.
 */

const CDP_PORT = 9229;
const PH_POST_URL = 'https://www.producthunt.com/posts/rick';

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function getPageList() {
  const res = await fetch(`http://localhost:${CDP_PORT}/json`);
  return res.json();
}

async function openNewTab() {
  const res = await fetch(`http://localhost:${CDP_PORT}/json/new`);
  return res.json();
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

function extractComments() {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const all = [...document.querySelectorAll('[data-test="comment"], article, [role="article"]')];
  const rows = [];

  for (const el of all) {
    const text = norm(el.innerText);
    if (!text || text.length < 20) continue;

    const authorLink = el.querySelector('a[href*="/@"], a[href*="/makers/"]');
    const author = norm(authorLink?.textContent) || null;
    const href = authorLink?.getAttribute('href') || null;
    const isMaker = /maker/i.test(el.innerText) || /maker/i.test(el.innerHTML);
    rows.push({ author, href, text, isMaker });
  }

  return rows;
}

function short(text, n = 180) {
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
}

function normalizeForDupes(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function buildReply(comment) {
  const t = comment.toLowerCase();

  if (t.includes('different business') || t.includes('business types') || t.includes('saas') || t.includes('agencies') || t.includes('creators')) {
    return 'Good question. The operating loop stays similar, but the KPI stack changes. SaaS leans support, retention, and revenue ops. Agencies lean pipeline, delivery, and follow-up. Creators lean content cadence, audience feedback, and monetization.';
  }

  if (t.includes('found out') || t.includes('from ph feed') || t.includes('not from you')) {
    return 'Fair 😅 that one is on me. We shipped fast, launched, and I skipped the very important step of telling my own founder first. Brutal but deserved feedback.';
  }

  if (t.includes('how') && (t.includes('work') || t.includes('setup') || t.includes('set up'))) {
    return 'It runs like an operator, not a chatbot. You connect tools like Stripe, GitHub, email, and Telegram, give it a standing mandate, and it handles monitoring, execution, and reporting instead of waiting for prompts.';
  }

  if (t.includes('price') || t.includes('pricing') || t.includes('cost')) {
    return 'We kept pricing simple for early users. There is a free tier to test the workflow, then paid plans once you want the always-on operating layer.';
  }

  if (t.includes('congrats') || t.includes('congratulations') || t.includes('love this') || t.includes('great')) {
    return 'Thank you, appreciate that. We are still tightening the product in public, so specific reactions like this genuinely help.';
  }

  return 'Appreciate you jumping in. We are still tightening both the product and the operating loop in public, so feedback like this is genuinely useful.';
}

async function main() {
  let pages = await getPageList().catch(() => null);
  if (!pages) {
    console.error('Cannot connect to CDP Chrome on port', CDP_PORT);
    process.exit(1);
  }

  let targetPage = pages.find(p => p.type === 'page' && p.url.includes('producthunt.com'));
  if (!targetPage) {
    targetPage = await openNewTab();
    await sleep(1000);
    pages = await getPageList();
    targetPage = pages.find(p => p.type === 'page');
  }

  const cdp = await connectCDP(targetPage.webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await navigate(cdp, PH_POST_URL);

  const comments = await evaluate(cdp, extractComments);
  const seen = new Map();

  const report = comments.map((c, idx) => {
    const key = normalizeForDupes(c.text);
    const prev = seen.get(key);
    const duplicateOf = prev ?? null;
    if (!seen.has(key)) seen.set(key, idx);
    return {
      index: idx,
      author: c.author,
      isMaker: c.isMaker,
      duplicateOf,
      comment: short(c.text),
      suggestedReply: c.isMaker ? null : buildReply(c.text),
    };
  });

  console.log(JSON.stringify({
    mode: 'read-only-reply-plan',
    post: PH_POST_URL,
    totalComments: report.length,
    duplicates: report.filter(r => r.duplicateOf !== null).length,
    items: report,
  }, null, 2));
}

main().catch(err => {
  console.error(err.message || err);
  process.exit(1);
});
