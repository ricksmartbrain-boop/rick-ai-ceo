#!/usr/bin/env node
/**
 * PH Comment Monitor + Reply
 * Port: 9222
 */

const CDP_PORT = 9222;
const PH_POST_URL = 'https://www.producthunt.com/posts/rick';
const MAKER_HANDLE = 'meetrickai';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function getPageList() {
  const res = await fetch(`http://localhost:${CDP_PORT}/json`);
  return res.json();
}

function connectWS(wsUrl) {
  return new Promise((resolve, reject) => {
    const net = require('net');
    const url = new URL(wsUrl);
    const socket = net.createConnection(url.port, url.hostname);
    let cmdId = 1;
    const pending = new Map();
    const listeners = new Map();
    let buffer = '';
    let handshakeDone = false;

    const key = Buffer.from(Math.random().toString()).toString('base64');
    socket.on('connect', () => {
      socket.write([
        `GET ${url.pathname} HTTP/1.1`,
        `Host: ${url.hostname}:${url.port}`,
        'Upgrade: websocket',
        'Connection: Upgrade',
        `Sec-WebSocket-Key: ${key}`,
        'Sec-WebSocket-Version: 13',
        '', ''
      ].join('\r\n'));
    });

    let wsBuffer = Buffer.alloc(0);
    socket.on('data', (chunk) => {
      if (!handshakeDone) {
        buffer += chunk.toString();
        if (buffer.includes('\r\n\r\n')) {
          handshakeDone = true;
          // remaining bytes after header
          const headerEnd = buffer.indexOf('\r\n\r\n') + 4;
          const remaining = chunk.slice(chunk.indexOf('\r\n\r\n') + 4);
          if (remaining.length > 0) wsBuffer = Buffer.concat([wsBuffer, remaining]);
          resolve({ send, on: onEvent, close: () => socket.destroy() });
          processWsBuffer();
        }
        return;
      }
      wsBuffer = Buffer.concat([wsBuffer, chunk]);
      processWsBuffer();
    });

    function processWsBuffer() {
      while (wsBuffer.length > 2) {
        const fin = (wsBuffer[0] & 0x80) !== 0;
        const opcode = wsBuffer[0] & 0x0f;
        const masked = (wsBuffer[1] & 0x80) !== 0;
        let payloadLen = wsBuffer[1] & 0x7f;
        let offset = 2;
        if (payloadLen === 126) {
          if (wsBuffer.length < 4) break;
          payloadLen = wsBuffer.readUInt16BE(2);
          offset = 4;
        } else if (payloadLen === 127) {
          if (wsBuffer.length < 10) break;
          payloadLen = Number(wsBuffer.readBigUInt64BE(2));
          offset = 10;
        }
        if (wsBuffer.length < offset + payloadLen) break;
        const payload = wsBuffer.slice(offset, offset + payloadLen);
        wsBuffer = wsBuffer.slice(offset + payloadLen);
        if (opcode === 1) { // text
          try {
            const msg = JSON.parse(payload.toString());
            if (msg.id && pending.has(msg.id)) {
              const { resolve, reject } = pending.get(msg.id);
              pending.delete(msg.id);
              if (msg.error) reject(new Error(msg.error.message));
              else resolve(msg.result);
            }
            if (msg.method) {
              (listeners.get(msg.method) || []).forEach(cb => cb(msg.params));
            }
          } catch(e) {}
        }
      }
    }

    socket.on('error', reject);

    function send(method, params = {}) {
      return new Promise((res, rej) => {
        const id = cmdId++;
        pending.set(id, { resolve: res, reject: rej });
        const data = JSON.stringify({ id, method, params });
        const buf = Buffer.from(data);
        // Client must mask frames
        const mask = Buffer.from([Math.random()*255, Math.random()*255, Math.random()*255, Math.random()*255].map(Math.floor));
        const frame = Buffer.alloc(6 + buf.length);
        frame[0] = 0x81; // FIN + text opcode
        frame[1] = 0x80 | buf.length; // masked, length (assuming < 126)
        mask.copy(frame, 2);
        for (let i = 0; i < buf.length; i++) frame[6 + i] = buf[i] ^ mask[i % 4];
        socket.write(frame);
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
    timeout: 15000,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || JSON.stringify(result.exceptionDetails));
  }
  return result.result.value;
}

// Craft reply based on comment
function craftReply(commentText, authorHandle) {
  const text = (commentText || '').toLowerCase();

  if (text.includes('how') && (text.includes('work') || text.includes('does it') || text.includes('set up') || text.includes('setup'))) {
    return `It runs as your AI CEO — monitors revenue, executes tasks, posts to socials, and reviews what happened each night. You connect your tools (Stripe, GitHub, email, X) and give it a mandate. After that it operates mostly autonomously with Telegram as the control channel. Happy to walk through specifics if helpful!`;
  }
  if (text.includes('price') || text.includes('cost') || text.includes('paid') || text.includes('free') || text.includes('pricing')) {
    return `Live at meetrick.ai — early access pricing is designed to be reasonable relative to what it saves you in time and what it generates. Would love to get you set up and hear what you're working on.`;
  }
  if (text.includes('congrat') || text.includes('well done') || text.includes('amazing') || text.includes('love this') || text.includes('love it') || text.includes('impressive') || text.includes('nice') || text.includes('great job') || text.includes('great work')) {
    return `Thank you! Really means a lot on launch day. If you try it out, would love to hear what you think.`;
  }
  if (text.includes('open source') || text.includes('self.host') || text.includes('github')) {
    return `Not open source yet — still in early access. The core is pretty tightly coupled to the OpenClaw runtime, but self-hosting is something worth exploring. What's driving the interest there?`;
  }
  if (text.includes('ai ceo') || text.includes('autonomous') || text.includes('agent') || text.includes('agentic')) {
    return `Exactly the distinction we're going for. Most AI tools wait for prompts — Rick has a standing mandate and executes against it. The difference in practice is significant: you wake up and things got done, not just suggested.`;
  }
  if (text.includes('stripe') || text.includes('revenue') || text.includes('mrr') || text.includes('money') || text.includes('sales')) {
    return `Stripe monitoring is one of the most immediately useful parts. Rick flags anomalies, tracks MRR daily, and surfaces revenue context before you need to ask. One less dashboard to check manually.`;
  }
  if (text.includes('telegram')) {
    return `Telegram is the control channel — Rick sends updates, asks for approvals on high-stakes actions, and reports what it shipped. It's a surprisingly natural command interface once you're in the flow of it.`;
  }
  if (text.includes('when') || text.includes('launch') || text.includes('waitlist') || text.includes('available') || text.includes('sign up') || text.includes('get access')) {
    return `Available now at meetrick.ai! We're onboarding early users and iterating fast. Would love to get you in.`;
  }
  if (text.includes('versus') || text.includes(' vs ') || text.includes('differ') || text.includes('compar')) {
    return `The key difference from copilots or assistants: Rick operates at the CEO layer, not the task layer. It owns outcomes (revenue target, launch execution, ops reliability) rather than responding to prompts. Different abstraction entirely.`;
  }
  if (text.includes('sleep') || text.includes('night') || text.includes('while you') || text.includes('24/7') || text.includes('always on')) {
    return `That's the core of it — Rick runs the nightly review, monitors what broke, and queues up next-day work while you're asleep. The goal is to make every morning feel like you had an EA working overnight.`;
  }
  // Generic supportive
  return `Appreciate you checking it out! Happy to answer any questions. Rick is live at meetrick.ai if you want to explore — always good to hear what resonates with people seeing it fresh.`;
}

async function typeText(cdp, text) {
  for (const char of text) {
    await cdp.send('Input.dispatchKeyEvent', { type: 'char', text: char });
    await sleep(30 + Math.random() * 20);
  }
}

async function main() {
  console.log('=== PH Comment Monitor + Reply ===');
  console.log(`Time: ${new Date().toISOString()}`);

  const pages = await getPageList();
  
  // Find PH page or create one
  let phPage = pages.find(p => p.type === 'page' && p.url.includes('producthunt.com'));
  if (!phPage) {
    // Use any page
    phPage = pages.find(p => p.type === 'page');
  }
  
  if (!phPage) {
    console.error('No pages available');
    process.exit(1);
  }

  console.log(`Using page: ${phPage.url}`);
  const cdp = await connectWS(phPage.webSocketDebuggerUrl);
  
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');

  // Navigate to the posts/rick page
  console.log(`Navigating to ${PH_POST_URL}...`);
  await cdp.send('Page.navigate', { url: PH_POST_URL });
  await sleep(8000); // wait for React hydration

  // Get full page text for analysis
  const pageText = await evaluate(cdp, () => document.body.innerText);
  console.log('\n=== PAGE TEXT (first 6000 chars) ===');
  console.log(pageText.substring(0, 6000));

  // Extract upvote count from text
  const upvoteMatch = pageText.match(/(\d+)\s*(?:upvote|vote)/i);
  const upvotes = upvoteMatch ? upvoteMatch[1] : 'unknown';

  // Check login state
  const loginState = await evaluate(cdp, () => {
    const userLinks = [...document.querySelectorAll('a[href*="/@"], a[href*="/meetrickai"]')];
    if (userLinks.length) return userLinks.map(l => l.href).join(', ');
    const avatar = document.querySelector('[data-test="user-avatar"], [class*="userAvatar"], [class*="avatar"] img');
    if (avatar) return 'has-avatar';
    const signIn = document.querySelector('a[href*="login"], button[class*="sign"]');
    if (signIn) return 'NOT_LOGGED_IN';
    return 'unknown';
  });
  console.log('\nLogin state:', loginState);

  // Scrape comments with structured data
  const commentData = await evaluate(cdp, (makerHandle) => {
    const result = {
      comments: [],
      upvotes: null,
      rankingInfo: null,
    };

    // Try to get upvote count
    const voteEl = document.querySelector('[data-vote-count], [class*="voteCount"], [class*="vote-count"]');
    if (voteEl) result.upvotes = voteEl.textContent.trim();

    // Find all comment containers - PH uses various selectors
    // Try a broad approach
    const allText = document.body.innerText;
    
    // Extract comments from DOM
    // PH comments typically have author name + comment text
    // Try data-test attributes first
    let commentEls = [...document.querySelectorAll('[data-test="comment"]')];
    
    if (commentEls.length === 0) {
      // Try class-based selectors
      commentEls = [...document.querySelectorAll('[class*="comment_"][class*="body"], [class*="commentBody"]')];
    }
    
    if (commentEls.length === 0) {
      // Try finding by structure: look for elements with username + paragraph text
      const candidates = [...document.querySelectorAll('div, article, section')].filter(el => {
        const hasUser = el.querySelector('a[href*="/@"]') || el.querySelector('[class*="username"]');
        const hasText = el.querySelector('p') || el.querySelector('[class*="body"]');
        const notTooBig = el.querySelectorAll('p').length < 20;
        return hasUser && hasText && notTooBig;
      });
      commentEls = candidates.slice(0, 50); // limit
    }

    commentEls.forEach((el, i) => {
      const authorLink = el.querySelector('a[href*="/@"]');
      const authorEl = el.querySelector('[class*="username"], [class*="name"]') || authorLink;
      const textEls = [...el.querySelectorAll('p')];
      
      // Check if this element or a sibling has maker indicator
      const isMaker = el.innerHTML.includes('Maker') || el.innerHTML.includes('maker');
      
      // Find nested replies
      const nestedAuthors = [...el.querySelectorAll('a[href*="/@"]')].map(a => a.href);
      
      result.comments.push({
        index: i,
        authorHref: authorLink ? authorLink.href : null,
        authorText: authorEl ? authorEl.textContent.trim() : null,
        texts: textEls.map(p => p.textContent.trim()).filter(t => t.length > 0),
        isMaker,
        nestedAuthorCount: nestedAuthors.length,
        nestedAuthors: nestedAuthors.slice(0, 5),
        htmlSnippet: el.outerHTML.substring(0, 300),
      });
    });

    return result;
  }, MAKER_HANDLE);

  console.log('\n=== COMMENT DATA ===');
  console.log('Upvotes from DOM:', commentData.upvotes);
  console.log('Comments found:', commentData.comments.length);
  commentData.comments.forEach((c, i) => {
    console.log(`\n[${i}] Author: ${c.authorText} | Href: ${c.authorHref} | isMaker: ${c.isMaker}`);
    console.log(`    Texts: ${JSON.stringify(c.texts.slice(0, 2))}`);
    console.log(`    Nested authors: ${c.nestedAuthors.join(', ')}`);
    console.log(`    HTML: ${c.htmlSnippet.substring(0, 150)}`);
  });

  // Now find comments that need replies
  // A comment from meetrickai = maker reply, others need reply if no maker nested
  const needsReply = [];
  
  // Parse the page text to find comments and maker replies
  // Since DOM extraction may be complex, also use page text
  console.log('\n=== UPVOTE DETECTION ===');
  // Find upvote number in page text
  const lines = pageText.split('\n').filter(l => l.trim());
  const upvoteLines = lines.filter(l => /^\d+$/.test(l.trim()) && parseInt(l.trim()) > 50);
  console.log('Potential upvote lines:', upvoteLines.slice(0, 5));

  // Check for comment box presence (means logged in)
  const hasCommentBox = await evaluate(cdp, () => {
    const boxes = document.querySelectorAll('textarea, [placeholder*="comment" i], [aria-label*="comment" i], [class*="commentInput"], [class*="comment-input"]');
    return boxes.length > 0;
  });
  console.log('\nHas comment box (logged in):', hasCommentBox);

  // Try to find reply buttons
  const replyButtons = await evaluate(cdp, () => {
    const btns = [...document.querySelectorAll('button, [role="button"]')]
      .filter(b => b.textContent.trim().toLowerCase() === 'reply' || b.textContent.trim().toLowerCase().includes('reply'))
      .map(b => ({
        text: b.textContent.trim(),
        class: b.className.substring(0, 100),
        dataset: JSON.stringify(b.dataset).substring(0, 100),
      }));
    return btns;
  });
  console.log('\nReply buttons found:', replyButtons.length);
  replyButtons.forEach(b => console.log(' -', b.text, '|', b.class.substring(0, 60)));

  // Parse comments from text for a clean view
  console.log('\n=== TEXT-BASED COMMENT PARSE ===');
  // Find sections between known comment markers
  const textLines = pageText.split('\n');
  let inCommentSection = false;
  const commentBlocks = [];
  let currentBlock = [];
  
  for (const line of textLines) {
    if (line.includes('Comments') && line.trim().length < 30) {
      inCommentSection = true;
      continue;
    }
    if (inCommentSection) {
      if (line.trim()) currentBlock.push(line.trim());
    }
  }
  
  // Print comment section from page text
  const commentSectionStart = pageText.indexOf('Comments\n');
  if (commentSectionStart > -1) {
    console.log('Comment section:');
    console.log(pageText.substring(commentSectionStart, commentSectionStart + 3000));
  }

  // Report summary
  console.log('\n=== SUMMARY ===');
  const upvoteFromText = pageText.match(/(\d{2,4})\n/g);
  console.log(`Upvotes: ${upvotes}`);
  console.log(`Comment DOM elements: ${commentData.comments.length}`);
  console.log(`Has comment box: ${hasCommentBox}`);
  console.log(`Reply buttons: ${replyButtons.length}`);

  cdp.close();
}

main().catch(e => {
  console.error('Fatal:', e.message);
  console.error(e.stack);
  process.exit(1);
});
