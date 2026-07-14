const WS = require('/Users/rickthebot/.openclaw/workspace/node_modules/ws');
const http = require('http');
const url = process.argv[2];
if (!url) { console.error('Usage: node check-reddit-comment.js <url>'); process.exit(1); }
http.get('http://localhost:9223/json/list', res => {
  let d = '';
  res.on('data', c => d += c);
  res.on('end', () => {
    const tabs = JSON.parse(d);
    const tab = tabs.find(t => t.type === 'page' && t.url.includes('reddit.com'));
    if (!tab) { console.log('no tab'); process.exit(1); }
    const ws = new WS(tab.webSocketDebuggerUrl);
    ws.on('open', () => {
      ws.send(JSON.stringify({ id: 1, method: 'Page.navigate', params: { url } }));
    });
    ws.on('message', msg => {
      const m = JSON.parse(msg.toString());
      if (m.id === 1) {
        setTimeout(() => {
          ws.send(JSON.stringify({
            id: 2,
            method: 'Runtime.evaluate',
            params: {
              expression: `(function(){
                const comments=[...document.querySelectorAll('.comment')].slice(0,60).map(c=>({
                  author:c.querySelector('.author')?.textContent?.trim(),
                  body:c.querySelector('.md')?.innerText?.slice(0,220)
                }));
                return JSON.stringify(comments.filter(x=>x.author==='MeetRickAI'));
              })()`,
              returnByValue: true
            }
          }));
        }, 4000);
      }
      if (m.id === 2) {
        console.log(m.result.result.value);
        ws.close();
      }
    });
  });
});
