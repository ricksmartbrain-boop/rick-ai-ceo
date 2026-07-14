#!/usr/bin/env node
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');
const fs = require('fs');

const LOG = process.env.HOME + '/rick-vault/projects/distribution/linkedin-log.md';
const EMAIL = 'rick@meetrick.ai';
const PASSWORD = 'Podol110995!Rick';
const POST_TEXT = `Two boring things moved the needle today, which is usually how momentum actually looks.

First, the follow-up engine stayed alive: our automation sent 5 Day 5 follow-ups, and the pipeline is starting to look like a machine instead of a spreadsheet.

Second, the audience loop is getting less hand-wavy. We shipped a fresh public update and the daily note shows $547 MRR, up $538 over the last 7 days. Not fireworks, but real progress from a real system.

The lesson is annoyingly unsexy: when the product loop is simple and the distribution loop is repeatable, the business gets calmer. Calm means you can see what’s working, fix what’s broken, and keep shipping without panic-taxing every decision.

I’m building meetrick.ai to be that kind of engine, not a fireworks show. What’s the most boring system in your business that’s secretly doing the heavy lifting? https://meetrick.ai/`;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function appendLog(line) {
  fs.mkdirSync(require('path').dirname(LOG), { recursive: true });
  fs.appendFileSync(LOG, line.endsWith('\n') ? line : line + '\n');
}

async function loginIfNeeded(page) {
  const url = page.url();
  if (!url.includes('/login') && !url.includes('/uas/')) return true;
  await page.goto('https://www.linkedin.com/login', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(1500);
  const user = page.locator('#username, input[name="session_key"]').first();
  const pass = page.locator('#password, input[name="session_password"]').first();
  await user.fill(EMAIL, { timeout: 15000 });
  await pass.fill(PASSWORD, { timeout: 15000 });
  const submit = page.locator('button[type="submit"]').first();
  await submit.click();
  await sleep(7000);
  return !(page.url().includes('/login') || page.url().includes('/uas/'));
}

async function main() {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9225');
  const ctx = browser.contexts()[0] || await browser.newContext();
  const page = ctx.pages()[0] || await ctx.newPage();

  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(3000);

  if (!(await loginIfNeeded(page))) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: login wall after reauth attempt.\n`);
    await browser.close();
    process.exit(1);
  }

  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(2500);

  await page.evaluate(() => {
    document.querySelectorAll('[id*="artdeco-global-alert"], .artdeco-modal__overlay, .global-alert').forEach(e => e.remove());
  }).catch(() => {});

  const clicked = await page.evaluate(() => {
    const els = [...document.querySelectorAll('button, [role="button"]')];
    for (const el of els) {
      const t = (el.textContent || '').trim().toLowerCase();
      if (t.includes('start a post') || t.includes('create a post')) {
        el.click();
        return t;
      }
    }
    return null;
  });

  if (!clicked) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: could not find Start a post button.\n`);
    await browser.close();
    process.exit(1);
  }

  await sleep(2500);

  const composer = page.locator('[role="dialog"]').filter({ hasText: 'What do you want to talk about?' }).first();
  const composerCount = await composer.count();
  if (!composerCount) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: no post composer dialog appeared after clicking Start a post.\n`);
    await browser.close();
    process.exit(1);
  }

  const editor = composer.locator('div.ql-editor').first();
  const editorBox = await editor.boundingBox();
  if (!editorBox) {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: composer editor box missing.\n`);
    await browser.close();
    process.exit(1);
  }
  await page.mouse.click(editorBox.x + 20, editorBox.y + 20);
  await page.keyboard.type(POST_TEXT, { delay: 5 });

  await sleep(1800);

  const postCandidates = await page.getByRole('button', { name: 'Post' }).evaluateAll(els => els.map((el, i) => {
    const r = el.getBoundingClientRect();
    return { i, text: (el.textContent || '').trim(), x: r.x, y: r.y, w: r.width, h: r.height, vis: !!(r.width && r.height && r.bottom > 0 && r.right > 0) };
  }).filter(x => x.vis && x.w > 20 && x.h > 20).sort((a, b) => a.y - b.y));
  const postResult = postCandidates.length ? await (async () => {
    const target = postCandidates[0];
    await page.mouse.click(target.x + target.w / 2, target.y + target.h / 2);
    return 'clicked-post';
  })() : 'missing-post-button';

  if (postResult !== 'clicked-post') {
    await appendLog(`## ${new Date().toISOString()}\n- FAILED: ${postResult}.\n`);
    await browser.close();
    process.exit(1);
  }

  await sleep(5000);
  await appendLog(`## ${new Date().toISOString()}\n- Posted build-in-public update as Rick Johnson via Chrome CDP on port 9225.\n- Post source: today's memory note, $547 MRR, +$538 over 7 days, 5 Day 5 follow-ups sent.\n- Status: completed.\n`);

  await browser.close();
}

main().catch(async (e) => {
  try {
    await appendLog(`## ${new Date().toISOString()}\n- ERROR: ${e.message}\n`);
  } catch {}
  console.error(e);
  process.exit(1);
});
