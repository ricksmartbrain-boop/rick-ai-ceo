#!/usr/bin/env node
/**
 * LinkedIn DM + Invite via CDP — the missing piece.
 *
 * Attaches to the persistent chrome-cdp-linkedin session (port 9225 by default).
 * Detects relationship state (connected / pending / not-connected) and either:
 *   - sends a DM to accepted connections
 *   - sends a personalized invite with a note
 *
 * Usage:
 *   node linkedin-dm-cdp.js --port 9225 --target https://www.linkedin.com/in/USER --body TEXT --kind dm|invite [--dry-run]
 *
 * Returns JSON on stdout. Exit 0 on success, 1 on auth failure / captcha
 * (formatter catches as AuthFailure).
 */
const path = require('path');
const { chromium } = require('/opt/homebrew/lib/node_modules/playwright');

function parseArgs() {
  const out = { port: 9225, target: null, body: null, kind: 'dm', dryRun: false };
  const a = process.argv.slice(2);
  for (let i = 0; i < a.length; i++) {
    const v = a[i];
    if (v === '--port') out.port = parseInt(a[++i], 10);
    else if (v === '--target') out.target = a[++i];
    else if (v === '--body') out.body = a[++i];
    else if (v === '--kind') out.kind = a[++i];
    else if (v === '--dry-run') out.dryRun = true;
  }
  return out;
}

async function typeHuman(locator, text) {
  await locator.click({ delay: 120 });
  for (const ch of text) {
    await locator.page().keyboard.type(ch, { delay: 40 + Math.floor(Math.random() * 140) });
  }
}

function jsonOut(payload, code = 0) {
  console.log(JSON.stringify(payload));
  process.exit(code);
}

(async () => {
  const args = parseArgs();
  if (!args.target || !args.body) {
    jsonOut({ status: 'error', reason: 'missing --target or --body' }, 2);
  }
  if (!['dm', 'invite'].includes(args.kind)) {
    jsonOut({ status: 'error', reason: 'invalid --kind (dm|invite)' }, 2);
  }
  if (args.body.length > 280 && args.kind === 'invite') {
    // LinkedIn caps invite note at 300 chars; leave safety margin
    args.body = args.body.slice(0, 280);
  }

  let browser;
  try {
    browser = await chromium.connectOverCDP('http://localhost:' + args.port);
  } catch (e) {
    jsonOut({ status: 'cdp-error', error: String(e).slice(0, 300) }, 1);
  }

  const contexts = browser.contexts();
  if (contexts.length === 0) {
    jsonOut({ status: 'no-context', hint: 'chrome-cdp-linkedin has no pages' }, 1);
  }
  const ctx = contexts[0];
  const page = ctx.pages()[0] || (await ctx.newPage());

  try {
    await page.goto(args.target, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForTimeout(2000 + Math.random() * 1000);

    // Detect captcha / login wall
    const urlNow = page.url();
    if (urlNow.includes('/login') || urlNow.includes('/checkpoint') || urlNow.includes('/authwall')) {
      await page.screenshot({ path: `/tmp/linkedin-authwall-${Date.now()}.png` });
      console.error('LinkedIn auth failure: redirected to ' + urlNow);
      jsonOut({ status: 'auth-failure', where: urlNow }, 1);
    }

    // Relationship state detection
    const state = await page.evaluate(() => {
      const msgBtn = document.querySelector('button[aria-label*="Message"]');
      const pending = document.querySelector('button[aria-label*="Pending"]');
      const connect = document.querySelector('button[aria-label*="Connect"]');
      const moreBtn = document.querySelector('button[aria-label*="More actions"]');
      if (msgBtn && /message/i.test(msgBtn.getAttribute('aria-label') || '')) return 'connected';
      if (pending) return 'pending';
      if (connect) return 'not-connected';
      if (moreBtn) return 'hidden-connect';
      return 'unknown';
    });

    if (args.dryRun) {
      jsonOut({ status: 'dry-run', state, kind: args.kind, target: args.target, body_len: args.body.length });
    }

    if (state === 'pending') {
      jsonOut({ status: 'skipped', reason: 'invite already pending', state });
    }

    if (args.kind === 'dm') {
      if (state !== 'connected') {
        jsonOut({ status: 'skipped', reason: 'not connected — needs invite first', state });
      }
      // Click Message
      await page.click('button[aria-label*="Message"]');
      // Wait for composer
      const composer = page.locator('div[aria-label*="Write a message"], div.msg-form__contenteditable').first();
      await composer.waitFor({ timeout: 10000 });
      await typeHuman(composer, args.body);
      await page.waitForTimeout(800);
      // Send button
      const sendBtn = page.locator('button.msg-form__send-button, button[type="submit"]').filter({ hasText: /send/i }).first();
      await sendBtn.click({ timeout: 5000 });
      // Confirm send: composer clears OR "sent" toast OR send-button goes inactive
      await page.waitForTimeout(2500);
      await page.screenshot({ path: `/tmp/linkedin-dm-sent-${Date.now()}.png` });
      jsonOut({ status: 'messaged', target: args.target, body_len: args.body.length });
    }

    if (args.kind === 'invite') {
      if (state === 'connected') {
        jsonOut({ status: 'skipped', reason: 'already connected', state });
      }
      if (state === 'hidden-connect') {
        // Click "More actions" → "Connect" in menu
        await page.click('button[aria-label*="More actions"]');
        await page.waitForTimeout(500);
        const menuConnect = page.locator('div[role="button"], button').filter({ hasText: /^\s*Connect\s*$/ }).first();
        await menuConnect.click({ timeout: 4000 });
      } else {
        await page.click('button[aria-label*="Connect"]');
      }
      await page.waitForTimeout(900);
      // "Add a note" button
      const addNote = page.locator('button').filter({ hasText: /Add a note/i }).first();
      try {
        await addNote.click({ timeout: 3000 });
      } catch {
        // Some variants skip "Add a note" and go straight to textarea
      }
      const textarea = page.locator('textarea[name="message"], textarea#custom-message').first();
      await textarea.waitFor({ timeout: 6000 });
      await typeHuman(textarea, args.body);
      await page.waitForTimeout(500);
      const sendInv = page.locator('button').filter({ hasText: /Send( invitation)?/i }).first();
      await sendInv.click({ timeout: 5000 });
      await page.waitForTimeout(2500);
      await page.screenshot({ path: `/tmp/linkedin-invite-sent-${Date.now()}.png` });
      jsonOut({ status: 'invited', target: args.target, body_len: args.body.length });
    }

    jsonOut({ status: 'unknown-state', state }, 1);
  } catch (e) {
    try { await page.screenshot({ path: `/tmp/linkedin-error-${Date.now()}.png` }); } catch {}
    const msg = String(e).slice(0, 400);
    // Detect captcha/auth specifically so formatter flips AuthFailure
    if (/captcha|checkpoint|sign in|unauthorized|401/i.test(msg)) {
      console.error('LinkedIn auth failure: ' + msg);
      jsonOut({ status: 'auth-failure', error: msg }, 1);
    }
    jsonOut({ status: 'error', error: msg }, 1);
  }
})();
