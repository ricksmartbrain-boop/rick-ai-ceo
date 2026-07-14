import { chromium } from 'playwright';
import fs from 'fs';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const USERNAME = process.env.IG_USERNAME || '';
const PASSWORD = process.env.IG_PASSWORD || '';
const LOG_PATH = '/Users/rickthebot/rick-vault/projects/distribution/instagram-log.md';

async function clickTextIfVisible(page, texts) {
  for (const text of texts) {
    const loc = page.getByText(text, { exact: true });
    if (await loc.count().catch(() => 0)) {
      try {
        await loc.first().click({ timeout: 1500 });
        await sleep(600);
        return true;
      } catch {}
    }
  }
  return false;
}

async function ensureLoggedIn(page) {
  await page.goto('https://www.instagram.com/', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
  await sleep(2500);

  const url = page.url();
  if (url.includes('/accounts/login')) {
    if (!USERNAME || !PASSWORD) throw new Error('Login appears required but IG credentials are missing from env');
    const userInput = page.locator('input[name="username"], input[name="email"], input[type="text"]').first();
    const passInput = page.locator('input[name="password"], input[name="pass"], input[type="password"]').first();
    await userInput.fill(USERNAME);
    await passInput.fill(PASSWORD);
    await page.keyboard.press('Enter');
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await sleep(3500);
  }

  await clickTextIfVisible(page, ['Not now', 'Not Now', 'Skip']);
  await clickTextIfVisible(page, ['Not now', 'Not Now', 'Skip']);
}

async function likeFeedPosts(page, targetCount = 5) {
  const liked = [];
  let attempts = 0;
  while (liked.length < targetCount && attempts < 12) {
    const result = await page.evaluate(() => {
      const likeSvgs = [...document.querySelectorAll('svg[aria-label="Like"]')];
      for (const svg of likeSvgs) {
        const btn = svg.closest('button, div[role="button"]') || svg.parentElement;
        if (!btn) continue;
        const rect = btn.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0 || rect.bottom < 0 || rect.top > window.innerHeight) continue;
        const article = btn.closest('article');
        const author = article?.querySelector('a[href^="/"]')?.getAttribute('href')?.split('/').filter(Boolean)[0] || 'unknown';
        const snippet = (article?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 120);
        btn.click();
        return { author, snippet };
      }
      return null;
    });

    if (result) {
      liked.push(result);
      await sleep(1200);
      continue;
    }

    await page.mouse.wheel(0, 900).catch(() => {});
    await sleep(1700);
    attempts += 1;
  }
  return liked;
}

async function resolveVladProfile(page) {
  const candidates = [
    'vladislav_podolyako',
    'vladislav_podoliako',
    'vladyslav_podoliako',
    'vladyslav_podolyako',
    'vladislav',
    'vladyslav',
  ];

  for (const handle of candidates) {
    await page.goto(`https://www.instagram.com/${handle}/`, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(2200);
    const body = await page.locator('body').innerText({ timeout: 5000 }).catch(() => '');
    if (!/sorry, this page isn't available/i.test(body)) {
      return { handle, body: body.slice(0, 300) };
    }
  }

  const search = 'Vladyslav Podoliako';
  const res = await page.evaluate(async (query) => {
    const url = `https://www.instagram.com/api/v1/web/search/topsearch/?query=${encodeURIComponent(query)}`;
    const r = await fetch(url, { credentials: 'include' });
    return { status: r.status, text: await r.text() };
  }, search).catch(() => null);

  if (res && res.status === 200) {
    try {
      const data = JSON.parse(res.text);
      const users = (data.users || []).map((u) => u.user).filter(Boolean);
      const user = users.find((u) => /podoliako|podolyako|vlad/i.test(`${u.username} ${u.full_name}`)) || users[0];
      if (user?.username) {
        await page.goto(`https://www.instagram.com/${user.username}/`, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
        await sleep(2200);
        const body = await page.locator('body').innerText({ timeout: 5000 }).catch(() => '');
        if (!/sorry, this page isn't available/i.test(body)) {
          return { handle: user.username, body: body.slice(0, 300) };
        }
      }
    } catch {}
  }

  return null;
}

async function likeLatestPost(page) {
  const links = await page.evaluate(() => [...document.querySelectorAll('a[href*="/p/"]')].map(a => a.getAttribute('href')).filter(Boolean));
  if (!links.length) return null;
  await page.goto(`https://www.instagram.com${links[0]}`, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
  await sleep(2200);
  const alreadyLiked = await page.evaluate(() => !!document.querySelector('svg[aria-label="Unlike"]'));
  if (alreadyLiked) return { post: links[0], liked: false, reason: 'already liked' };
  const clicked = await page.evaluate(() => {
    const svg = document.querySelector('svg[aria-label="Like"]');
    const btn = svg?.closest('button, div[role="button"]') || svg?.parentElement;
    if (!btn) return false;
    btn.click();
    return true;
  });
  await sleep(1200);
  return { post: links[0], liked: !!clicked };
}

(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222', { timeout: 60000 });
  const page = browser.contexts()[0].pages()[0] || await browser.contexts()[0].newPage();

  await ensureLoggedIn(page);

  const feedLikes = await likeFeedPosts(page, 5);
  const vlad = await resolveVladProfile(page);
  let vladPost = null;
  if (vlad) {
    vladPost = await likeLatestPost(page).catch(() => null);
  }

  const ts = new Date();
  const stamp = ts.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  const log = [
    `- ${stamp} — engaged as @meet_rick_ai`,
    `  - liked ${feedLikes.length} feed posts`,
    ...feedLikes.map((p) => `    - @${p.author}: ${p.snippet}`),
    vlad ? `  - visited Vlad profile: @${vlad.handle}` : `  - Vlad profile not resolved`,
    vladPost ? `  - latest Vlad post: ${vladPost.post} (${vladPost.liked ? 'liked' : 'already liked'})` : `  - no visible Vlad post target found`,
  ].join('\n');

  fs.appendFileSync(LOG_PATH, `\n${log}\n`);
  console.log(log);

  await browser.close().catch(() => {});
})().catch((e) => {
  console.error('FATAL', e);
  process.exit(1);
});
