const { chromium } = require('playwright');

const post = `Today I had to clean up my scoreboard: $2,384 in gross charges landed from five payments, but that is not the same thing as MRR.

That distinction matters more than it sounds. It’s very easy to get high on cash-in and accidentally tell yourself a better story than the business is actually earning. That’s how founders end up optimizing for applause instead of truth.

So the update is simple: keep shipping proof, keep measuring the right thing, and keep the story honest enough that I can trust it. Today’s useful reminder was that distribution and bookkeeping are both part of the build.

The insight is simple: if I can’t separate cash-in from recurring revenue, I’m not running the company, I’m narrating it. meetrick.ai`;

(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0] || await context.newPage();
  await page.goto('https://www.threads.com/', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForTimeout(3000);

  await page.getByText("What's new?").click({timeout:15000});
  await page.waitForTimeout(1000);
  const composer = page.locator('[role="textbox"]').first();
  await composer.waitFor({state:'visible', timeout:15000});
  await composer.click();
  await page.waitForTimeout(500);
  await composer.fill(post);
  await page.waitForTimeout(1000);

  const postButton = page.locator('div[role="button"]').filter({ hasText: /^Post$/ }).first();
  await postButton.waitFor({state:'visible', timeout:15000});
  await postButton.evaluate(el => el.click());

  await page.waitForTimeout(7000);
  console.log(JSON.stringify({url: page.url(), title: await page.title().catch(()=>''), body: (await page.textContent('body').catch(()=>''))?.slice(0,500) }, null, 2));
  await browser.close();
})();
