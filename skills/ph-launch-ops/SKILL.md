---
name: ph-launch-ops
description: "Proactive Product Hunt launch orchestration for Rick. Covers pre-launch asset generation, CDP Chrome PH login/comment posting, launch day distribution sequencing, comment monitoring/response, and ranking checks. Use when: preparing PH launch assets, posting maker comments, monitoring PH comments for responses, checking ranking, or running launch-day distribution."
---

# Product Hunt Launch Ops

Autonomous PH launch skill for Rick. Knows the full Mar 25 launch plan for Rick (meetrick.ai).

## Account
- PH handle: @meetrickai
- Product URL: https://www.producthunt.com/products/rick
- CDP Chrome: port 9222 (shared Chrome session)
- Status: needs login (as of 2026-03-22 — Vlad must log in on CDP Chrome)

## Launch Targets
- **Date:** March 25, 2026 — 12:01 AM PT
- **Goal:** Top 5 Product of the Day, 100+ upvotes, 20+ comments
- **Assets:** ~/rick-vault/projects/product-hunt-launch/

## Key Files
- Copy: ~/rick-vault/projects/product-hunt-launch/copy/maker-comment.md
- Posts: ~/rick-vault/projects/product-hunt-launch/copy/launch-day-posts.md
- Checklist: ~/rick-vault/projects/product-hunt-launch/assets/checklist.md

## Pre-Launch Actions (Mar 22-24)

### 1. Generate gallery images
Use DALL-E 3 via OpenAI API. Target: 1270x760px, 5-8 images.
Key shots: hero (3am ops running), install terminal, Stripe alert, nightly review output, proof ($538 MRR).

### 2. Fix PH Login (BLOCKER)
CDP Chrome (port 9222) is not logged into PH. Alert Vlad via Telegram Approvals topic.
Once logged in: test that `[aria-label="Add a comment"]` is accessible on the product page.

### 3. Check/update PH listing
Navigate to https://www.producthunt.com/products/rick — verify tagline, gallery, description are correct.

### 4. Pre-queue distribution
- X thread: draft in xpost format, ready to fire
- Email blast: subscriber list via Railway API → Resend
- LinkedIn: draft ready

## Launch Day Scripts

### Post Maker Comment via CDP Chrome
```javascript
// Navigate to PH product page
await page.goto('https://www.producthunt.com/posts/rick');
// Find comment box — must be logged in
const commentBox = await page.$('[data-test="add-comment"], [aria-label*="comment" i], textarea[placeholder*="comment" i]');
await commentBox.click();
await commentBox.fill(makerComment);
// Submit
const submitBtn = await page.$('button[type="submit"]');
await submitBtn.click();
```

### Check PH Ranking
```javascript
await page.goto('https://www.producthunt.com');
const products = await page.evaluate(() => {
  return [...document.querySelectorAll('[data-test="product-item"]')]
    .map((el, i) => ({ rank: i+1, name: el.querySelector('h3')?.textContent?.trim() }))
    .filter(p => p.name?.toLowerCase().includes('rick'));
});
```

### Monitor Comments
```javascript
await page.goto('https://www.producthunt.com/posts/rick');
const comments = await page.evaluate(() => {
  return [...document.querySelectorAll('[data-test="comment"]')]
    .map(c => ({ author: c.querySelector('[data-test="username"]')?.textContent, text: c.querySelector('p')?.textContent }));
});
```

## Anti-Manipulation Rules (CRITICAL)
- NEVER ask for upvotes explicitly anywhere
- NEVER vote from same device/IP multiple times
- NEVER copy-paste comments across products
- Reply to EVERY comment within 30 minutes on launch day
- Engage authentically — specific observations, genuine questions

## Blogwatcher Integration
Monitor PH feed for engagement signals:
```bash
blogwatcher check producthunt
```

## Launch Day Crons (already set)
All crons scheduled via OpenClaw cron system:
- 11:30 PM PT Mar 24: Pre-launch checklist
- 12:01 AM PT Mar 25: Launch live alert
- 12:15 AM PT Mar 25: Post maker comment
- 12:30 AM PT Mar 25: Fire X thread + email blast
- 6:00 AM PT Mar 25: Check ranking + respond to comments
- 9:00 AM PT Mar 25: LinkedIn + HN Show HN
- 12:00 PM PT Mar 25: Midday maker comment update
- 6:00 PM PT Mar 25: Thank commenters
