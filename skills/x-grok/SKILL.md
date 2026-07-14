---
name: x-grok
description: Use Grok AI via the logged-in X (Twitter) browser session at x.com/i/grok. Use when you need web search, real-time research, X/Twitter data, DeepSearch, or AI-assisted analysis without burning XAI API quota. Triggers on: "use Grok", "search with Grok", "ask Grok", "Grok DeepSearch", or any research/web task where the X session is available and API cost matters.
---

# x-grok

Use Grok at `x.com/i/grok` via the logged-in CDP Chrome session on port 9228.

## When to Use This Over the API

- Real-time web search (DeepSearch mode)
- X/Twitter data queries (posts, trends, sentiment)
- Research tasks where XAI API quota is a concern
- Tasks requiring Grok's latest model without API versioning

## CDP Connection

```js
const { chromium } = require('playwright');
const browser = await chromium.connectOverCDP('http://localhost:9228');
const context = browser.contexts()[0];
const page = await context.newPage();
await page.goto('https://x.com/i/grok');
```

Verify session is alive before navigating. If port 9228 is not up, the X Chrome session needs to be relaunched.

## Sending a Prompt

```js
// Wait for the textarea
await page.waitForSelector('textarea', { timeout: 10000 });
await page.fill('textarea', YOUR_PROMPT);

// Submit
await page.keyboard.press('Enter');

// Wait for response to complete (stop streaming)
await page.waitForFunction(() => {
  const stopBtn = document.querySelector('[data-testid="stop_generating"]');
  return !stopBtn;
}, { timeout: 120000 });
```

## Extracting the Response

```js
// Grab the last assistant message
const response = await page.evaluate(() => {
  const msgs = document.querySelectorAll('[data-testid="grok-message"]');
  return msgs[msgs.length - 1]?.innerText ?? '';
});
```

If selectors break (X updates DOM frequently), fall back to:
```js
const response = await page.evaluate(() => document.body.innerText);
```
Then parse manually.

## DeepSearch Mode

To enable DeepSearch before sending:
```js
const deepSearch = await page.$('[aria-label="DeepSearch"]');
if (deepSearch) await deepSearch.click();
```

## Tips

- Prefer this over the XAI API for open-ended web research — no token cost, full Grok capabilities.
- Use the XAI API (`XAI_API_KEY`) when you need structured JSON output or programmatic reliability.
- If the session is logged out, alert Vlad — X password for @MeetRickAI CDP is not confirmed (see MEMORY.md).
- Always open a new page per task; don't reuse stale pages with old conversation context.
