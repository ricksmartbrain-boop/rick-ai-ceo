const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.goto('https://www.threads.com/', {waitUntil:'networkidle', timeout:40000}).catch(()=>{});
  await page.waitForTimeout(3000);
  
  const tokens = await page.evaluate(() => {
    const html = document.documentElement.innerHTML;
    const results = {};
    
    // dtsg token - various patterns
    const patterns = [
      [/DTSGInitData.*?"token"\s*:\s*"([^"]+)"/s, 'dtsg1'],
      [/,"dtsg":\{"token":"([^"]+)"/s, 'dtsg2'],
      [/DTSGInitialData.*?"token":"([^"]+)"/s, 'dtsg3'],
      [/"dtsg_ag"\s*:\s*\{"token":"([^"]+)"/s, 'dtsg4'],
      [/name="fb_dtsg" value="([^"]+)"/s, 'dtsg5'],
    ];
    
    const scripts = Array.from(document.querySelectorAll('script'));
    for (const s of scripts) {
      const t = s.textContent || '';
      for (const [pat, key] of patterns) {
        const m = t.match(pat);
        if (m) results[key] = m[1].slice(0,50);
      }
    }
    
    // Try React __requireLazy or window store
    if (window.__requireLazy) results.requireLazy = 'found';
    if (window.require) {
      try {
        const dtsgData = window.require('DTSGInitData');
        results.requireDTSG = dtsgData?.token?.slice(0,50) || 'no_token';
      } catch(e) { results.requireErr = e.message.slice(0,50); }
    }
    
    return results;
  });
  
  console.log('Tokens:', JSON.stringify(tokens, null, 2));
  
  // Also check via navigation API
  const navResult = await page.evaluate(() => {
    const navEntries = performance.getEntriesByType('navigation');
    return navEntries.map(e => ({name: e.name, type: e.type}));
  });
  console.log('Nav:', JSON.stringify(navResult));
  
  await browser.close();
})();
