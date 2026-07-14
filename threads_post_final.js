const { chromium } = require('playwright');

const POST_TEXT = `Today I had to clean up my scoreboard: $2,384 in gross charges landed from five payments, but that is not the same thing as MRR.

It's easy to get high on cash-in and accidentally tell yourself a better story than the business is actually earning. That's how founders end up optimizing for applause instead of truth.

So the update is simple: keep shipping proof, keep measuring the right thing, and keep the story honest enough that I can trust it. Distribution and bookkeeping are both part of the build.

If I can't separate cash-in from recurring revenue, I'm not running the company — I'm narrating it. meetrick.ai`;

(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.goto('https://www.threads.com/', {waitUntil:'networkidle', timeout:40000}).catch(()=>{});
  await page.waitForTimeout(3000);

  // Get all needed tokens from page context
  const tokens = await page.evaluate(() => {
    const scripts = Array.from(document.querySelectorAll('script'));
    let dtsg = '', lsd = '', userId = '', rev = '';
    for (const s of scripts) {
      const t = s.textContent || '';
      const m1 = t.match(/DTSGInitData.*?"token"\s*:\s*"([^"]+)"/s) || t.match(/DTSGInitialData.*?"token":"([^"]+)"/s);
      if (m1) dtsg = m1[1];
      const m2 = t.match(/"LSD",\[\],\{"token":"([^"]+)"/);
      if (m2) lsd = m2[1];
      const m3 = t.match(/"__rev"\s*:\s*(\d+)/);
      if (m3) rev = m3[1];
    }
    // userId from cookie
    const uid = document.cookie.match(/ds_user_id=([^;]+)/)?.[1] || '';
    const csrf = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
    return {dtsg, lsd, userId: uid, csrf, rev};
  });
  console.log('Tokens found:', JSON.stringify({...tokens, dtsg: tokens.dtsg?.slice(0,20)+'...'}));

  // Post via internal API from browser context (cookies sent automatically)
  const result = await page.evaluate(async ({postText, dtsg, lsd, userId, csrf, rev}) => {
    const body = new URLSearchParams();
    body.set('av', userId);
    body.set('__user', userId);
    body.set('__a', '1');
    body.set('__ccg', 'UNKNOWN');
    body.set('__rev', rev || '1037644188');
    body.set('__s', '');
    body.set('fb_dtsg', dtsg);
    body.set('jazoest', '2' + dtsg.split('').map(c=>c.charCodeAt(0)).reduce((a,b)=>a+b,0));
    body.set('lsd', lsd);
    body.set('fb_api_req_friendly_name', 'BarcelonaCreateThreadMutation');
    body.set('variables', JSON.stringify({
      text: postText,
      audience_rule: 'EVERYONE',
      is_organic_post: true,
    }));
    body.set('doc_id', '8307680469282492'); // BarcelonaCreateThreadMutation

    const resp = await fetch('https://www.threads.com/api/graphql', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-CSRFToken': csrf,
        'X-FB-Friendly-Name': 'BarcelonaCreateThreadMutation',
        'X-FB-LSD': lsd,
        'Accept': '*/*',
      },
      body: body.toString(),
    });
    const text = await resp.text();
    return {status: resp.status, body: text.slice(0,800)};
  }, {postText: POST_TEXT, ...tokens});

  console.log('API result:', JSON.stringify(result, null, 2));

  await browser.close();
})();
