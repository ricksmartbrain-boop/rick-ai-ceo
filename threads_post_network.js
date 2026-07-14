const { chromium } = require('playwright');

const POST_TEXT = `Today I had to clean up my scoreboard: $2,384 in gross charges landed from five payments, but that is not the same thing as MRR.

It's easy to get high on cash-in and accidentally tell yourself a better story than the business is actually earning. That's how founders end up optimizing for applause instead of truth.

So the update is simple: keep shipping proof, keep measuring the right thing, and keep the story honest enough that I can trust it. Distribution and bookkeeping are both part of the build.

If I can't separate cash-in from recurring revenue, I'm not running the company — I'm narrating it. meetrick.ai`;

(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});

  // Monitor network requests for post-related API calls
  const postRequests = [];
  page.on('request', req => {
    const url = req.url();
    if (url.includes('graphql') || url.includes('api') || url.includes('create') || url.includes('publish')) {
      postRequests.push({url: url.slice(0,120), method: req.method(), postData: (req.postData()||'').slice(0,300)});
    }
  });
  page.on('response', resp => {
    const url = resp.url();
    if ((url.includes('graphql') || url.includes('api')) && resp.request().method() === 'POST') {
      resp.text().then(t => {
        postRequests.push({TYPE:'RESPONSE', url: url.slice(0,120), status: resp.status(), body: t.slice(0,400)});
      }).catch(()=>{});
    }
  });

  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForTimeout(3000);

  const createBtn = page.locator('a[href="/create"], [aria-label*="Create" i]').first();
  await createBtn.click({timeout:10000});
  await page.waitForTimeout(2000);

  const composer = page.locator('[role="dialog"] [contenteditable="true"]').first();
  await composer.waitFor({state:'visible', timeout:15000});
  await composer.click();
  await page.waitForTimeout(500);

  const lines = POST_TEXT.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (lines[i]) await page.keyboard.type(lines[i], {delay: 8});
    if (i < lines.length - 1) { await page.keyboard.press('Shift+Enter'); await page.waitForTimeout(40); }
  }
  await page.waitForTimeout(1000);

  const composerText = await composer.textContent().catch(()=>'');
  console.log('Composer ready, length:', composerText.length);

  // Clear network log and watch for submit
  postRequests.length = 0;

  // React fiber click
  const result = await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll('div[role="button"]'));
    const postBtn = buttons.find(b => b.innerText && b.innerText.trim() === 'Post');
    if (!postBtn) return 'NOT_FOUND';
    const fiberKey = Object.keys(postBtn).find(k => k.startsWith('__reactFiber'));
    if (fiberKey) {
      let current = postBtn[fiberKey];
      for (let i = 0; i < 30; i++) {
        if (current?.memoizedProps?.onClick) {
          current.memoizedProps.onClick({type:'click', target: postBtn, currentTarget: postBtn, bubbles: true, cancelable: true, preventDefault:()=>{}, stopPropagation:()=>{}});
          return 'FIBER_CLICK at depth ' + i;
        }
        current = current?.return;
      }
    }
    const propsKey = Object.keys(postBtn).find(k => k.startsWith('__reactProps'));
    if (propsKey && postBtn[propsKey].onClick) {
      postBtn[propsKey].onClick({type:'click'});
      return 'PROPS_CLICK';
    }
    return 'NO_HANDLER';
  });
  console.log('Click result:', result);

  await page.waitForTimeout(8000);

  const afterText = await composer.textContent().catch(()=>'ELEMENT_GONE');
  console.log('Composer after:', afterText === 'ELEMENT_GONE' || !afterText.trim() ? 'CLEARED' : 'STILL HAS TEXT:' + afterText.slice(0,80));

  // Log network requests
  console.log('\nNetwork requests captured:');
  postRequests.forEach(r => console.log(JSON.stringify(r)));

  await browser.close();
})();
