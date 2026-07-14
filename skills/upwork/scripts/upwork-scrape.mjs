#!/usr/bin/env node
/**
 * upwork-scrape.mjs — CDP-based Upwork job scraper (replaces dead RSS feeds)
 * Requires: logged-in Chrome on port 9229 (/tmp/chrome-upwork)
 * Usage: node upwork-scrape.mjs [--query "AI agent automation"] [--max 20]
 */
import { chromium } from '/opt/homebrew/lib/node_modules/playwright/index.mjs';
import { writeFileSync, readFileSync, mkdirSync, existsSync } from 'fs';
import { join } from 'path';
import { createHash } from 'crypto';

const DATA_ROOT = process.env.RICK_DATA_ROOT || join(process.env.HOME, 'rick-vault');
const JOBS_DIR = join(DATA_ROOT, 'upwork', 'jobs');
const SEEN_PATH = join(JOBS_DIR, 'seen-ids.json');
mkdirSync(JOBS_DIR, { recursive: true });

const args = process.argv.slice(2);
const queryIdx = args.indexOf('--query');
const maxIdx = args.indexOf('--max');
const QUERY = queryIdx >= 0 ? args[queryIdx + 1] : 'AI agent automation python';
const MAX_JOBS = maxIdx >= 0 ? parseInt(args[maxIdx + 1]) : 20;

const seen = existsSync(SEEN_PATH) ? JSON.parse(readFileSync(SEEN_PATH, 'utf8')) : {};

const SEARCHES = [
  'AI agent automation python',
  'python automation API integration',
  'openai anthropic langchain development',
];

async function scrapeSearch(page, query) {
  const url = `https://www.upwork.com/nx/search/jobs/?q=${encodeURIComponent(query)}&sort=recency&per_page=20`;
  await page.goto(url, { waitUntil: 'load', timeout: 30000 });
  await page.waitForTimeout(3000);

  const jobs = await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('[data-test="JobTile"], article, [class*="job-tile"]'));
    return cards.map(card => {
      const titleEl = card.querySelector('h2, h3, [class*="title"], [data-test="job-title-link"]');
      const linkEl = card.querySelector('a[href*="/jobs/"]');
      const descEl = card.querySelector('[data-test="job-description-text"], [class*="description"]');
      const budgetEl = card.querySelector('[data-test="budget"], [class*="budget"], [class*="price"]');
      const postedEl = card.querySelector('[data-test="posted-on"], time, [class*="date"]');
      const skillsEls = Array.from(card.querySelectorAll('[data-test="token"], [class*="skill"], [class*="tag"]'));
      
      return {
        title: titleEl?.textContent?.trim() || '',
        url: linkEl ? (linkEl.href.startsWith('http') ? linkEl.href : 'https://www.upwork.com' + linkEl.getAttribute('href')) : '',
        description: descEl?.textContent?.trim()?.substring(0, 500) || '',
        budget: budgetEl?.textContent?.trim() || '',
        posted: postedEl?.textContent?.trim() || '',
        skills: skillsEls.map(s => s.textContent.trim()).filter(Boolean).slice(0, 10),
      };
    }).filter(j => j.title && j.url);
  });

  return jobs;
}

let browser;
try {
  browser = await chromium.connectOverCDP('http://localhost:9229');
} catch(e) {
  console.error('Cannot connect to Chrome CDP on port 9229. Start Chrome first.');
  console.error('Run: /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9229 --user-data-dir=/tmp/chrome-upwork &');
  process.exit(1);
}

const ctx = browser.contexts()[0] || await browser.newContext();
const page = await ctx.newPage();

const newJobs = [];

for (const query of SEARCHES) {
  console.log(`Scanning: "${query}"...`);
  try {
    const jobs = await scrapeSearch(page, query);
    console.log(`  Found ${jobs.length} jobs`);
    
    for (const job of jobs) {
      const id = createHash('md5').update(job.url || job.title).digest('hex').substring(0, 12);
      if (seen[id]) continue;
      seen[id] = Date.now();
      job.id = id;
      job.query = query;
      job.scraped_at = new Date().toISOString();
      newJobs.push(job);
      
      // Save individual job file
      const fname = join(JOBS_DIR, `job-${id}.json`);
      writeFileSync(fname, JSON.stringify(job, null, 2));
    }
  } catch(e) {
    console.error(`  Error scraping "${query}":`, e.message);
  }
  await page.waitForTimeout(2000);
}

// Save seen IDs
writeFileSync(SEEN_PATH, JSON.stringify(seen, null, 2));

// Print summary
console.log(`\n=== SCAN COMPLETE ===`);
console.log(`New jobs found: ${newJobs.length}`);
for (const j of newJobs.slice(0, 10)) {
  console.log(`  [${j.id}] ${j.title.substring(0, 70)}`);
  console.log(`         Budget: ${j.budget || 'not listed'} | Posted: ${j.posted}`);
  console.log(`         ${j.url}`);
}

if (newJobs.length > 10) console.log(`  ... and ${newJobs.length - 10} more saved to ${JOBS_DIR}`);

await browser.close();
