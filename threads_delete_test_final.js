const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const page = browser.contexts()[0].pages()[0];
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto('https://www.threads.com/@meet_rick_ai/post/DXR5TbaCnkz', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(5000);

  // Open overflow menu on the post
  await page.locator('div[role="button"]').nth(5).click({force:true});
  await page.waitForTimeout(1500);

  // Click Delete in menu
  const deleteBtn = page.getByText('Delete', { exact: true }).first();
  await deleteBtn.waitFor({state:'visible', timeout:10000});
  await deleteBtn.click({force:true});
  await page.waitForTimeout(1500);

  // Confirm delete if second dialog appears
  const confirmDelete = page.getByText('Delete', { exact: true }).last();
  const count = await page.getByText('Delete', { exact: true }).count();
  console.log('Delete text count after first click:', count);
  if (count > 1) {
    await confirmDelete.click({force:true}).catch(()=>{});
    await page.waitForTimeout(3000);
  }

  // Verify post gone by reloading post URL
  await page.goto('https://www.threads.com/@meet_rick_ai/post/DXR5TbaCnkz', {waitUntil:'domcontentloaded', timeout:30000}).catch(()=>{});
  await page.waitForTimeout(5000);
  const body = await page.textContent('body').catch(()=> '');
  const gone = body.includes("link's not working") || body.includes('page is gone') || !body.includes('Testing post format');
  console.log('POST_DELETED:', gone);
  console.log(body.slice(0,1200));
  await browser.close();
  process.exit(gone ? 0 : 1);
})();
