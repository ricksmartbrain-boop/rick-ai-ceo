'use strict';
const http = require('http');
const WebSocket = require('ws');

const CDP_PORT = 9222;
const MY_HANDLE = 'meetrickai';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function cdpFetch(path) {
  return new Promise((resolve, reject) => {
    http.get(`http://localhost:${CDP_PORT}${path}`, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch(e) { reject(e); } });
    }).on('error', reject);
  });
}

class CDP {
  constructor(wsUrl) {
    this.id = 1;
    this.pending = new Map();
    this.ws = new WebSocket(wsUrl);
    this.ready = new Promise((res, rej) => {
      this.ws.on('open', res);
      this.ws.on('error', rej);
    });
    this.ws.on('message', raw => {
      const msg = JSON.parse(raw);
      if (msg.id && this.pending.has(msg.id)) {
        const cb = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) cb.reject(new Error(JSON.stringify(msg.error)));
        else cb.resolve(msg.result);
      }
    });
  }
  send(method, params) {
    params = params || {};
    const id = this.id++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`Timeout: ${method}`));
        }
      }, 25000);
    });
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' | ' + JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
  async navigate(url) {
    await this.send('Page.navigate', { url });
    await sleep(5000);
  }
  close() { this.ws.close(); }
}

async function main() {
  const pages = await cdpFetch('/json/list');
  console.log('Available pages:');
  pages.filter(p => p.type === 'page').forEach(p => console.log(' -', p.url.substring(0,80)));
  
  // Use a products/rick tab  
  let targetPage = pages.find(p => p.url.includes('producthunt.com/products/rick') && p.type === 'page');
  if (!targetPage) targetPage = pages.find(p => p.url.includes('producthunt.com') && p.type === 'page');
  
  console.log('\nUsing tab:', targetPage.url);
  
  const cdp = new CDP(targetPage.webSocketDebuggerUrl);
  await cdp.ready;
  await cdp.send('Page.enable');
  
  // Navigate to posts/rick
  console.log('Navigating to https://www.producthunt.com/posts/rick ...');
  await cdp.navigate('https://www.producthunt.com/posts/rick');
  
  // Check where we landed
  const currentUrl = await cdp.eval('window.location.href');
  console.log('Landed on:', currentUrl);
  
  // Scroll to load all content
  await cdp.eval('window.scrollTo(0, 800)');
  await sleep(1500);
  await cdp.eval('window.scrollTo(0, document.body.scrollHeight / 2)');
  await sleep(1500);
  await cdp.eval('window.scrollTo(0, document.body.scrollHeight)');
  await sleep(2500);
  
  // Get page state
  const pageState = await cdp.eval(`
    JSON.stringify({
      title: document.title,
      url: window.location.href,
      bodyLength: document.body.innerText.length,
      // Find vote count
      votes: (() => {
        // Look for vote button with number
        const btns = [...document.querySelectorAll('button, [role="button"]')];
        for (const btn of btns) {
          const t = btn.textContent.trim();
          if (/^\\d{2,4}$/.test(t)) {
            const parentText = btn.parentElement?.textContent || '';
            if (parentText.toLowerCase().includes('upvote') || 
                btn.closest('[aria-label*="upvote" i]') || 
                btn.closest('[class*="vote" i]')) {
              return t;
            }
          }
        }
        // Broader: any standalone number 50-9999
        for (const btn of btns) {
          const t = btn.textContent.trim();
          if (/^\\d{2,4}$/.test(t) && parseInt(t) > 30) return t + '(approx)';
        }
        // From text
        const m = document.body.innerText.match(/(\\d{2,4})\\s*upvote/i);
        return m ? m[1] : 'not found';
      })()
    })
  `);
  const ps = JSON.parse(pageState);
  console.log('\nPage state:', ps);
  
  // Get all user links + surrounding text to identify comments
  const commentData = await cdp.eval(`
    JSON.stringify((() => {
      // Find all @username links on the page (not in nav)
      const allUserLinks = [...document.querySelectorAll('a[href*="/@"]')]
        .filter(a => {
          // Exclude nav/sidebar elements
          const nav = a.closest('nav, header, footer, [role="navigation"]');
          return !nav && a.href.includes('/@');
        });
      
      // Group by closest comment-like container
      // PH typically wraps each comment in a div with: img (avatar), username, text
      const seen = new Set();
      const comments = [];
      
      for (const link of allUserLinks) {
        const handle = link.href.split('/@')[1]?.split('/')[0]?.split('?')[0];
        if (!handle || seen.has(handle + link.closest('div')?.textContent?.substring(0,30))) continue;
        
        // Find the comment container - go up until we find one with a paragraph
        let container = link.parentElement;
        let depth = 0;
        while (container && depth < 6) {
          if (container.querySelector('p') && container.querySelectorAll('a[href*="/@"]').length <= 4) {
            break;
          }
          container = container.parentElement;
          depth++;
        }
        
        if (!container) continue;
        
        const key = handle + '|' + container.textContent?.substring(0,50);
        if (seen.has(key)) continue;
        seen.add(key);
        
        const allLinksInContainer = [...container.querySelectorAll('a[href*="/@"]')]
          .map(a => a.href.split('/@')[1]?.split('/')[0]?.split('?')[0])
          .filter(Boolean);
        
        const textEls = [...container.querySelectorAll('p')];
        const commentText = textEls.map(p => p.textContent.trim()).join(' ').substring(0, 400);
        
        const hasMyReply = allLinksInContainer.includes('meetrickai');
        
        comments.push({
          author: link.textContent.trim(),
          handle,
          text: commentText,
          allHandlesInThread: [...new Set(allLinksInContainer)],
          hasMyReply,
          isMine: handle === 'meetrickai'
        });
      }
      
      return {
        totalUserLinks: allUserLinks.length,
        comments: comments.slice(0, 30)
      };
    })())
  `);
  
  const cd = JSON.parse(commentData);
  console.log('\n=== COMMENTS FOUND ===');
  console.log('Total user links:', cd.totalUserLinks);
  cd.comments.forEach((c, i) => {
    console.log('\n[' + (i+1) + '] @' + c.handle + ' (' + (c.isMine ? 'MINE' : 'OTHER') + '): "' + c.text.substring(0,150) + '"');
    console.log('  Handles in thread:', c.allHandlesInThread.join(', '));
    console.log('  Has my reply:', c.hasMyReply);
  });
  
  // Filter comments that need replies (not mine, not already replied)
  const needsReply = cd.comments.filter(c => !c.isMine && !c.hasMyReply && c.text.length > 5);
  console.log('\n=== NEED REPLY: ' + needsReply.length + ' comments ===');
  needsReply.forEach(c => console.log(' - @' + c.handle + ': "' + c.text.substring(0,100) + '"'));
  
  if (needsReply.length === 0) {
    console.log('No unanswered comments found. All good!');
    
    // Also check the leaderboard ranking
    console.log('\n=== CHECKING LEADERBOARD ===');
    const leaderPage = pages.find(p => p.url.includes('leaderboard/daily/2026/3/25'));
    if (leaderPage) {
      const cdp2 = new CDP(leaderPage.webSocketDebuggerUrl);
      await cdp2.ready;
      
      const rankData = await cdp2.eval(`
        JSON.stringify((() => {
          const bodyText = document.body.innerText;
          // Find Rick in the list
          const lines = bodyText.split('\\n').filter(l => l.trim());
          const rickIdx = lines.findIndex(l => l.toLowerCase().includes('rick') && !l.toLowerCase().includes('rick ruby'));
          
          const snippets = rickIdx >= 0 ? lines.slice(Math.max(0, rickIdx-3), rickIdx+5) : ['Rick not found'];
          
          // Also try to find any ranking numbers near Rick  
          return {
            rickContext: snippets,
            allProducts: lines.slice(0, 60).join('|||')
          };
        })())
      `);
      const rd = JSON.parse(rankData);
      console.log('Rick context on leaderboard:', rd.rickContext);
      cdp2.close();
    }
  }
  
  cdp.close();
  process.exit(0);
}

main().catch(e => { console.error('FATAL:', e.message, e.stack); process.exit(1); });
