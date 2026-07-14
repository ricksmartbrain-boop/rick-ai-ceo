const CDP_PORT = 9225;
const PH_POST_URL = 'https://www.producthunt.com/posts/rick';

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
        if (opcode === 1) {
          try {
            const msg = JSON.parse(payload.toString());
            if (msg.id && pending.has(msg.id)) {
              const { resolve, reject } = pending.get(msg.id);
              pending.delete(msg.id);
              if (msg.error) reject(new Error(msg.error.message));
              else resolve(msg.result);
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
        const mask = Buffer.from([Math.random()*255, Math.random()*255, Math.random()*255, Math.random()*255].map(Math.floor));
        const frame = Buffer.alloc(6 + buf.length);
        frame[0] = 0x81;
        frame[1] = 0x80 | buf.length;
        mask.copy(frame, 2);
        for (let i = 0; i < buf.length; i++) frame[6 + i] = buf[i] ^ mask[i % 4];
        socket.write(frame);
      });
    }

    function onEvent(method, cb) {
      // Simplified - we don't need event listeners for this task
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

async function main() {
  console.log('=== Testing CDP Connection ===');
  
  let pages;
  try {
    pages = await getPageList();
    console.log(`Found ${pages.length} pages`);
  } catch (e) {
    console.error('Cannot connect to CDP Chrome on port', CDP_PORT);
    console.error(e.message);
    process.exit(1);
  }

  // Find PH page
  let phPage = pages.find(p => p.type === 'page' && p.url.includes('producthunt.com') && p.url.includes('/posts/rick'));
  if (!phPage) {
    phPage = pages.find(p => p.type === 'page' && p.url.includes('producthunt.com'));
  }
  if (!phPage) {
    phPage = pages.find(p => p.type === 'page');
  }
  
  if (!phPage) {
    console.error('No pages available');
    process.exit(1);
  }

  console.log(`Using page: ${phPage.url}`);
  const cdp = await connectWS(phPage.webSocketDebuggerUrl);
  
  // Enable needed domains
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('DOM.enable');
  // Note: Input domain doesn't need enable, we use dispatchKeyEvent directly
  
  // Navigate to the posts/rick page
  console.log(`Navigating to ${PH_POST_URL}...`);
  await cdp.send('Page.navigate', { url: PH_POST_URL });
  await sleep(8000); // wait for load

  // Get page title to verify
  const title = await evaluate(cdp, () => document.title);
  console.log(`Page title: ${title}`);
  
  // Check if we're on the right page
  const url = await evaluate(cdp, () => document.location.href);
  console.log(`Current URL: ${url}`);
  
  // Try to find comment box or login state
  const loginState = await evaluate(cdp, () => {
    // Check if we see signs of being logged in
    const userLinks = [...document.querySelectorAll('a[href*="/@"], a[href*="/meetrickai"]')];
    if (userLinks.length > 0) return `Logged in as: ${userLinks.map(l => l.href).join(', ')}`;
    
    const avatar = document.querySelector('[data-test="user-avatar"], img[alt*="avatar"], [class*="avatar"]');
    if (avatar) return 'Has avatar (likely logged in)';
    
    const signInButtons = [...document.querySelectorAll('a, button')]
      .filter(el => el.textContent.toLowerCase().includes('sign in') || 
                   el.textContent.toLowerCase().includes('log in'));
    if (signInButtons.length > 0) return 'Shows sign in buttons (NOT logged in)';
    
    return 'Cannot determine login state';
  });
  console.log(`Login state: ${loginState}`);
  
  // Get page text sample
  const pageText = await evaluate(cdp, () => document.body.innerText);
  console.log(`\n=== Page Text Sample (first 1500 chars) ===\n${pageText.substring(0, 1500)}`);
  
  // Look for comment-related elements
  const commentElements = await evaluate(cdp, () => {
    const selectors = [
      '[data-test*="comment"]',
      '[class*="comment"]',
      'textarea[placeholder*="comment" i]',
      '[aria-label*="comment" i]',
      'button:contains("Reply")',
      '[role="button"]:contains("reply")'
    ];
    const results = {};
    selectors.forEach(sel => {
      try {
        const els = document.querySelectorAll(sel);
        if (els.length > 0) results[sel] = els.length;
      } catch(e) {}
    });
    return results;
  });
  console.log(`\n=== Comment Elements Found ===\n`, JSON.stringify(commentElements, null, 2));
  
  await cdp.send('Page.disable');
  await cdp.send('Runtime.disable');
  await cdp.send('DOM.disable');
  
  const socket = cdp.ws;
  if (socket) socket.close();
  
  console.log('\n=== Test Complete ===');
}

main().catch(e => {
  console.error('Fatal:', e.message);
  console.error(e.stack);
  process.exit(1);
});