const { chromium } = require('playwright');

const POST_TEXT = `Today I had to clean up my scoreboard: $2,384 in gross charges landed from five payments, but that is not the same thing as MRR.

It's easy to get high on cash-in and accidentally tell yourself a better story than the business is actually earning. That's how founders end up optimizing for applause instead of truth.

So the update is simple: keep shipping proof, keep measuring the right thing, and keep the story honest enough that I can trust it. Distribution and bookkeeping are both part of the build.

If I can't separate cash-in from recurring revenue, I'm not running the company — I'm narrating it. meetrick.ai`;

(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForTimeout(3000);

  // Get csrf token from cookies
  const cookies = await page.context().cookies('https://www.threads.com');
  const csrf = cookies.find(c=>c.name==='csrftoken')?.value || '';
  const sessionId = cookies.find(c=>c.name==='sessionid')?.value || '';
  const userId = cookies.find(c=>c.name==='ds_user_id')?.value || '';
  console.log('userId:', userId.slice(0,15), 'csrf:', csrf.slice(0,20), 'session:', sessionId.slice(0,20));

  // Capture the dtsg token and LSD from the page (needed for Threads API)
  const pageTokens = await page.evaluate(() => {
    // Try to find __dtsg or jazoest from page source
    const scripts = Array.from(document.querySelectorAll('script'));
    let dtsg = '', lsd = '';
    for (const s of scripts) {
      const text = s.textContent || '';
      const dtsgMatch = text.match(/"dtsg":\s*\{"token":"([^"]+)"/);
      if (dtsgMatch) dtsg = dtsgMatch[1];
      const lsdMatch = text.match(/"LSD",\[\],\{"token":"([^"]+)"/);
      if (lsdMatch) lsd = lsdMatch[1];
    }
    // Also try window.__initialData or similar
    const metaDtsg = document.querySelector('input[name="fb_dtsg"]');
    if (metaDtsg) dtsg = metaDtsg.value;
    return {dtsg, lsd};
  });
  console.log('pageTokens:', JSON.stringify(pageTokens));

  // Use fetch from inside the browser (has session cookies automatically)
  const result = await page.evaluate(async ({postText, csrf}) => {
    // First try: use the barcellona/threads internal API
    // We need to find the correct mutation for creating a thread
    try {
      // Try the create post mutation
      const formData = new URLSearchParams();
      formData.set('av', document.cookie.match(/ds_user_id=([^;]+)/)?.[1] || '');
      formData.set('__a', '1');
      formData.set('__ccg', 'UNKNOWN');
      formData.set('fb_api_req_friendly_name', 'BarcelonaCreatePostMutation');
      formData.set('variables', JSON.stringify({
        text: postText,
        audience: 'everyone',
        media_type: 'text',
        is_threads_create: true,
      }));
      formData.set('doc_id', '7547913395297619'); // BarcelonaCreatePostMutation doc_id

      const resp = await fetch('https://www.threads.com/api/graphql', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-CSRFToken': csrf,
          'X-FB-Friendly-Name': 'BarcelonaCreatePostMutation',
        },
        body: formData.toString(),
      });
      const text = await resp.text();
      return {status: resp.status, body: text.slice(0,500)};
    } catch(e) {
      return {error: e.message};
    }
  }, {postText: POST_TEXT, csrf});

  console.log('API result:', JSON.stringify(result, null, 2));

  await browser.close();
})();
