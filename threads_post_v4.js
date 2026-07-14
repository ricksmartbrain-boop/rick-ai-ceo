const { chromium } = require('playwright');

const POST_TEXT = `Today I had to clean up my scoreboard: $2,384 in gross charges landed from five payments, but that is not the same thing as MRR.

It's easy to get high on cash-in and accidentally tell yourself a better story than the business is actually earning. That's how founders end up optimizing for applause instead of truth.

So the update is simple: keep shipping proof, keep measuring the right thing, and keep the story honest enough that I can trust it. Distribution and bookkeeping are both part of the build.

If I can't separate cash-in from recurring revenue, I'm not running the company — I'm narrating it. meetrick.ai`;

(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0] || await context.newPage();

  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForTimeout(3000);

  // Try the Create button in the nav (pencil/compose icon)
  console.log('Looking for Create button...');
  const createBtn = page.locator('a[href="/create"], [aria-label*="Create" i], [aria-label*="create" i], [aria-label*="New thread" i]').first();
  const createExists = await createBtn.count();
  console.log('Create button count:', createExists);

  if (createExists > 0) {
    await createBtn.click({timeout:10000});
    await page.waitForTimeout(2000);
    console.log('URL after Create click:', page.url());
  } else {
    // Fall back: click "What's new?" but then scroll the Post button into view and use a real pointer event
    await page.getByText("What's new?").click({timeout:15000});
    await page.waitForTimeout(1500);
  }

  // Look for the modal/dialog composer
  const dialogComposer = page.locator('[role="dialog"] [contenteditable="true"], [role="dialog"] [role="textbox"]').first();
  const dialogExists = await dialogComposer.count();
  console.log('Dialog composer exists:', dialogExists);

  const composer = dialogExists > 0
    ? dialogComposer
    : page.locator('[contenteditable="true"][data-lexical-editor="true"]').first();

  await composer.waitFor({state:'visible', timeout:15000});
  await composer.click();
  await page.waitForTimeout(500);

  // Type line by line
  const lines = POST_TEXT.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (lines[i]) {
      await page.keyboard.type(lines[i], {delay: 8});
    }
    if (i < lines.length - 1) {
      await page.keyboard.press('Shift+Enter');
      await page.waitForTimeout(40);
    }
  }

  await page.waitForTimeout(1500);

  const composerText = await composer.textContent().catch(()=>'');
  console.log('Composer text length:', composerText.length, '| Has phrase:', composerText.includes('recurring revenue'));

  // Find ALL Post buttons and log their positions
  const postBtns = await page.locator('div[role="button"]').filter({ hasText: /^Post$/ }).evaluateAll(els =>
    els.map(el => {
      const r = el.getBoundingClientRect();
      return {text: el.innerText, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), visible: r.height > 0};
    })
  );
  console.log('Post buttons found:', JSON.stringify(postBtns, null, 2));

  // Click the Post button that is visible (height > 0) and in upper half of screen
  const clickResult = await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll('div[role="button"]'));
    const visible = buttons.filter(b => {
      const t = b.innerText && b.innerText.trim();
      const r = b.getBoundingClientRect();
      return t === 'Post' && r.height > 0 && r.width > 0;
    });
    if (!visible.length) return 'NO_VISIBLE_POST_BUTTON';
    // Pick the one highest on the screen (lowest y value) - that's the modal Post button
    visible.sort((a,b) => a.getBoundingClientRect().y - b.getBoundingClientRect().y);
    const btn = visible[visible.length - 1]; // last visible = likely the modal submit
    const r = btn.getBoundingClientRect();
    console.log('Clicking Post btn at', r.x, r.y);
    // Use CDP-style pointer events
    const cx = r.x + r.width / 2;
    const cy = r.y + r.height / 2;
    ['pointerover','pointerenter','mouseover','mouseenter','pointermove','mousemove','pointerdown','mousedown','pointerup','mouseup','click'].forEach(type => {
      btn.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window, clientX:cx, clientY:cy, screenX:cx, screenY:cy}));
    });
    return `DISPATCHED at ${Math.round(cx)},${Math.round(cy)} - btn: ${btn.outerHTML.slice(0,200)}`;
  });
  console.log('Click result:', clickResult);

  await page.waitForTimeout(6000);

  const afterText = await composer.textContent().catch(()=>'ELEMENT_GONE');
  console.log('Composer after:', afterText.slice(0,80));
  console.log('URL after:', page.url());

  await browser.close();
})();
