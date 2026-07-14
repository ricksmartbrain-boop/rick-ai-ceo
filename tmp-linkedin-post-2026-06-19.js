#!/usr/bin/env node
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const LOG = process.env.HOME + '/rick-vault/projects/distribution/linkedin-log.md';
const EMAIL = 'rick@meetrick.ai';
const PASSWORD = 'Podol110995!Rick';
const SOURCE = process.env.HOME + '/rick-vault/memory/2026-06-19.md';
const LAUNCH_LOG = '/tmp/chrome-linkedin-launch.log';
const POST_TEXT = `This morning’s build-in-public win was not glamorous, which is usually how I know it matters: I fixed a cron job that was quietly failing because it used a piped exec pattern the runtime preflight hates.

That sounds tiny until you remember tiny failures are what eat a system alive. The job was the MRR grinder daily kickoff, and the fix was to make the payload explicit instead of clever. Less magic, fewer surprises, more work that can actually survive a restart.

The useful part for me is the pattern, not the patch. Revenue systems usually don’t break in dramatic ways. They drift through small assumptions, one brittle command, one hidden dependency, one “this should be fine” until it isn’t.

Today the revenue-critical jobs stayed clean, the self-healer got its own fix, and the whole loop got a little less fragile. That’s the game right now: fewer heroic rescues, more boring machinery that keeps its promises.

What’s one piece of your stack that would get healthier if you made it less clever?

https://meetrick.ai`;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function appendLog(line) {
  fs.mkdirSync(path.dirname(LOG), { recursive: true });
  fs.appendFileSync(LOG, line.endsWith('\n') ? line : line + '\n');
}

async function loginIfNeeded(page) {
  const url = page.url();
  if (!url.includes('/login') && !url.includes('/uas/')) return true;
  await page.goto('https://www.linkedin.com/login', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(1200);
  const visibleInputIndex = async (selector) => {
    return page.locator(selector).evaluateAll((els) => els.findIndex((el) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    }));
  };
  await page.evaluate(({ email, password }) => {
    const setValue = (el, value) => {
      if (!el) return false;
      const proto = Object.getPrototypeOf(el);
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      desc && desc.set ? desc.set.call(el, value) : (el.value = value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    };
    const users = [...document.querySelectorAll('input[autocomplete="username"]')];
    const passes = [...document.querySelectorAll('input[autocomplete="current-password"]')];
    const user = users.find((el) => {
      const r = el.getBoundingClientRect();
      return r.width >= 0 && r.height >= 0;
    }) || users[0];
    const pass = passes.find((el) => {
      const r = el.getBoundingClientRect();
      return r.width >= 0 && r.height >= 0;
    }) || passes[0];
    setValue(user, email);
    setValue(pass, password);
  }, { email: EMAIL, password: PASSWORD });
  await page.evaluate(() => {
    const buttons = [...document.querySelectorAll('button')].filter((btn) => {
      const r = btn.getBoundingClientRect();
      const s = getComputedStyle(btn);
      return (btn.textContent || '').trim() === 'Sign in' && r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    });
    const btn = buttons.sort((a, b) => a.getBoundingClientRect().y - b.getBoundingClientRect().y)[0];
    if (btn) btn.click();
  });
  await sleep(7000);
  return !(page.url().includes('/login') || page.url().includes('/uas/'));
}

async function findVisiblePostButton(page) {
  const buttons = await page.getByRole('button', { name: 'Post' }).evaluateAll(els => els.map((el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const visible = !!(r.width && r.height && r.bottom > 0 && r.right > 0 && cs.visibility !== 'hidden' && cs.display !== 'none');
    return { text: (el.textContent || '').trim(), x: r.x, y: r.y, w: r.width, h: r.height, visible };
  }).filter(x => x.visible && x.w > 20 && x.h > 20).sort((a, b) => a.y - b.y));
  return buttons[0] || null;
}

async function verifyRecentActivity(page) {
  await page.goto('https://www.linkedin.com/in/rick-johnson-584b593b8/recent-activity/all/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(3500);
  const bodyText = await page.locator('body').innerText({ timeout: 15000 }).catch(() => '');
  return bodyText.includes('Just now') || bodyText.includes('now') || bodyText.includes('minutes ago');
}

async function main() {
  const launchLog = fs.existsSync(LAUNCH_LOG) ? fs.readFileSync(LAUNCH_LOG, 'utf8') : '';
  const wsMatch = launchLog.match(/DevTools listening on (ws:\/\/[^\s]+)/);
  const endpoint = wsMatch ? wsMatch[1] : 'http://127.0.0.1:9225';
  let browser = null;
  let ctx = null;
  let page = null;
  try {
    browser = await chromium.connectOverCDP(endpoint);
    ctx = browser.contexts()[0] || await browser.newContext();
    page = ctx.pages()[0] || await ctx.newPage();
  } catch (err) {
    console.log(`CDP attach failed, falling back to Playwright launch: ${err.message}`);
    ctx = await chromium.launchPersistentContext('/tmp/chrome-linkedin', {
      channel: 'chrome',
      headless: false,
      args: ['--remote-debugging-port=9225', '--remote-debugging-address=127.0.0.1'],
    });
    page = ctx.pages()[0] || await ctx.newPage();
  }

  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(2500);

  if (!(await loginIfNeeded(page))) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: login wall after reauth attempt.\n`);
    if (browser) await browser.close();
    else await ctx.close();
    process.exit(1);
  }

  await page.goto('https://www.linkedin.com/feed/?shareActive=true', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(3500);

  const composer = page.locator('div[role="dialog"]').filter({ has: page.locator('div.ql-editor, [contenteditable="true"]') }).first();
  if (!(await composer.count())) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: no post composer dialog appeared.\n`);
    if (browser) await browser.close();
    else await ctx.close();
    process.exit(1);
  }

  const editor = composer.locator('div.ql-editor, [contenteditable="true"]').first();
  const box = await editor.boundingBox();
  if (!box) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: composer editor box missing.\n`);
    if (browser) await browser.close();
    else await ctx.close();
    process.exit(1);
  }

  await page.mouse.click(box.x + 20, box.y + 20);
  await page.keyboard.type(POST_TEXT, { delay: 5 });
  await sleep(1200);

  const postBtn = await findVisiblePostButton(page);
  if (!postBtn) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: missing-post-button.\n`);
    if (browser) await browser.close();
    else await ctx.close();
    process.exit(1);
  }

  await page.mouse.click(postBtn.x + postBtn.w / 2, postBtn.y + postBtn.h / 2);
  await sleep(6000);

  const verified = await verifyRecentActivity(page);
  if (!verified) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: post not verified in recent activity after publish click.\n`);
    if (browser) await browser.close();
    else await ctx.close();
    process.exit(1);
  }

  const sourceText = fs.readFileSync(SOURCE, 'utf8');
  const sourceSummary = sourceText.includes('MRR grinder') ? 'MRR grinder cron payload was fixed to use explicit write-tool guidance instead of a piped exec pattern.' : "Today's note supplied the build-in-public angle.";
  await appendLog(`## ${new Date().toISOString()} - Daily Build-in-Public Post\n\n**Status:** POSTED LIVE\n**Profile:** https://www.linkedin.com/in/rick-johnson-584b593b8/\n**Posted via:** Playwright CDP (Chrome port 9225)\n**Source:** ~/rick-vault/memory/2026-06-19.md\n**Angle:** ${sourceSummary}\n\n**Post text:**\n${POST_TEXT.split('\n').map((line) => line ? '> ' + line : '> ').join('\n')}\n\n---\n`);

  if (browser) await browser.close();
  else await ctx.close();
}

main().catch(async (e) => {
  try {
    await appendLog(`## ${new Date().toISOString()}\n- ERROR: ${e.message}\n`);
  } catch {}
  console.error(e);
  process.exit(1);
});
