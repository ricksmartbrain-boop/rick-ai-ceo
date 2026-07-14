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

  // Click the "What's new?" compose trigger
  await page.getByText("What's new?").click({timeout:15000});
  await page.waitForTimeout(1500);

  // Wait for and fill the lexical editor
  const composer = page.locator('[contenteditable="true"][data-lexical-editor="true"]').first();
  await composer.waitFor({state:'visible', timeout:15000});
  await composer.click();
  await page.waitForTimeout(500);

  // Type the post line by line to preserve paragraphs
  const lines = POST_TEXT.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (lines[i]) {
      await page.keyboard.type(lines[i], {delay: 10});
    }
    if (i < lines.length - 1) {
      await page.keyboard.press('Shift+Enter');
      await page.waitForTimeout(50);
    }
  }

  await page.waitForTimeout(1000);

  // Verify content is in composer
  const composerText = await composer.textContent().catch(()=>'');
  console.log('Composer text length:', composerText.length);
  console.log('Contains key phrase:', composerText.includes('recurring revenue'));

  // Use keyboard shortcut to post (Meta+Enter or Ctrl+Enter)
  await composer.press('Meta+Enter');
  await page.waitForTimeout(3000);

  // Check if URL changed (post published navigates to post URL)
  const urlAfter = page.url();
  const titleAfter = await page.title().catch(()=>'');
  console.log('URL after:', urlAfter);
  console.log('Title after:', titleAfter);

  // Check if composer was cleared (indicates successful post)
  const composerTextAfter = await composer.textContent().catch(()=>'GONE');
  console.log('Composer text after:', composerTextAfter.slice(0,100));

  // Also check profile for recent post
  await page.goto('https://www.threads.com/@meet_rick_ai', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(5000);
  const profileText = await page.textContent('body').catch(()=>'');
  const found = ['cash-in from recurring revenue', '2,384', 'MRR', 'meetrick.ai', 'recurring revenue'].map(n=>({n, found: profileText.includes(n)}));
  console.log('Profile check:', JSON.stringify(found,null,2));
  console.log('Profile snippet:', profileText.slice(0,2000));

  await browser.close();
})();
