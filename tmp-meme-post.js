const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
const fs = require('fs');

const [imagePath, caption] = process.argv.slice(2);
if (!imagePath || !caption) {
  console.error('Usage: node tmp-meme-post.js <imagePath> <caption>');
  process.exit(1);
}

async function postLinkedIn() {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9225');
  const context = browser.contexts()[0] || await browser.newContext();
  const page = context.pages()[0] || await context.newPage();

  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(5000);

  const startPost = page.locator('div').filter({ hasText: /^Start a post$/ }).first();
  const startBox = await startPost.boundingBox();
  if (!startBox) throw new Error('LinkedIn start post not found');
  const session = await page.context().newCDPSession(page);
  const sx = startBox.x + startBox.width / 2;
  const sy = startBox.y + startBox.height / 2;
  await session.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: sx, y: sy, buttons: 0 });
  await page.waitForTimeout(100);
  await session.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: sx, y: sy, button: 'left', clickCount: 1, buttons: 1 });
  await page.waitForTimeout(100);
  await session.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: sx, y: sy, button: 'left', clickCount: 1, buttons: 0 });
  await page.waitForTimeout(3000);

  const editor = page.locator('[role="dialog"] [contenteditable="true"], [role="dialog"] [role="textbox"], [contenteditable="true"], [role="textbox"]').first();
  await editor.waitFor({ state: 'visible', timeout: 20000 });
  await editor.click();
  await page.keyboard.type(caption, { delay: 15 });
  await page.waitForTimeout(1200);

  const photoButton = page.locator('[role="dialog"] button, [role="dialog"] div[role="button"], button, div[role="button"]').filter({ hasText: /^Photo$/i }).first();
  await photoButton.waitFor({ state: 'visible', timeout: 20000 });
  const photoBox = await photoButton.boundingBox();
  if (!photoBox) throw new Error('LinkedIn Photo button not found');
  const px = photoBox.x + photoBox.width / 2;
  const py = photoBox.y + photoBox.height / 2;
  await session.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: px, y: py, buttons: 0 });
  await page.waitForTimeout(100);
  await session.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: px, y: py, button: 'left', clickCount: 1, buttons: 1 });
  await page.waitForTimeout(100);
  await session.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: px, y: py, button: 'left', clickCount: 1, buttons: 0 });
  await page.waitForTimeout(2500);
  const fileInput = page.locator('input[type="file"]').first();
  if (await fileInput.count()) {
    await fileInput.setInputFiles(imagePath);
  } else {
    const chooserPromise = page.waitForEvent('filechooser', { timeout: 10000 }).catch(() => null);
    const altBtn = page.getByText(/Photo/i).first();
    if (await altBtn.count()) {
      await altBtn.evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })));
      const chooser = await chooserPromise;
      if (chooser) await chooser.setFiles(imagePath);
    }
  }
  await page.waitForTimeout(5000);

  const postButton = page.locator('button, [role="button"]').filter({ hasText: /^Post$/i }).first();
  await postButton.waitFor({ state: 'visible', timeout: 20000 });
  await postButton.click({ timeout: 10000 });
  await page.waitForTimeout(5000);

  const result = { url: page.url() };
  await browser.close();
  return result;
}

async function main() {
  const result = await postLinkedIn();
  console.log(JSON.stringify({ ok: true, ...result }));
}

main().catch(async err => {
  console.error(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
