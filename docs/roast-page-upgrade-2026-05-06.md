# /roast page — readability + conversion upgrade
Date: 2026-05-06 · Source: `~/meetrick-site/roast/index.html` (937 lines, single-file inline CSS+JS, identical to live `https://meetrick.ai/roast/`).

## TL;DR
Page already has solid bones (1,100+ roasts, 5+ min avg). Issues are not structural; they are
visual hierarchy + signaling. Five surgical upgrades. No font/color/aesthetic changes.

## Brief vs reality (corrections)
The brief described /roast as `Press Start 2P + Space Mono, yellow #FBBF24 on white,
1100px wrap, retro dot-grid`. Actual /roast is **dark theme** (`#0a0a0a` bg, `#ff3c3c` red
accent, `#00ff88` green accent, `#FBBF24` yellow accent on offer-cards, 760px wrap, no dot
grid). Press Start 2P + Space Mono is correct. Aesthetic preserved — all proposed edits stay
inside the existing palette.

## Audit findings

### 1. Above-the-fold hierarchy — OK
Hero → badge → H1 → subtitle → social-proof → form. Eye lands on the form within ~2s. Form
section is the dominant CTA; no competing element.

### 2. Headline contrast — minor issue
`h1` is `clamp(20px, 4vw, 32px)`, line-height 1.5, color `--fg #e8e8e0` with only the second
word "Business" in red. On 14" laptop the line-height collapses the two pixel-font lines
visually. Fix: bump line-height to 1.35 and keep first line dim, second line accented (already
the case) — this is a 1-line CSS tweak.

### 3. Social-proof legibility — biggest miss
Current bar: 12px font, "1,100+ founders" in green-bold but **same size** as the surrounding
sentence. The number that carries the trust is not visually anchored. Fix: pull the digit out
into its own larger pixel-font token and reduce the surrounding copy weight.

### 4. Form CTA — usable but undersized
Button is 10px Press Start 2P, `padding: 14px 22px`. On mobile it stacks below the input (good)
but the text is small. Also: the hint `Free. No signup. Takes ~10 seconds.` is a useful
reassurance — bump it to `13px` and add a subtle accent dot prefix for legibility.

### 5. Loading state — has a bar but no countdown
`loading-bar-fill` is a perpetual sliding gradient, not a true progress bar. Loading messages
cycle every 2.5s (good). Missing: a live elapsed-time counter ("Rick is reading… 7s") so the
user feels progress. The 45-attempt 1s poll loop already gives us per-second granularity for
free.

### 6. Result page UX — strong, with gaps
Roast renders in 16px Inter, 1.8 line-height (great for scanning). `**bold**` markdown
converted to `<strong>` with red glow. Email capture slides in 1.8s after roast. Two CTA
panels follow. The conversion architecture is sophisticated already.

**Gap:** the post-roast CTA sells `$9/mo Rick Pro + $97 Deep Roast + $499/mo Managed`. The brief
asks for the **Kit ($97) / Pilot (free) / This-Week** path. These are different products from
the existing Rick Pro / Deep Roast offers. Recommendation: **add a third "free path" panel**
above the existing paid offers — pilot is free (low-commitment), kit is the methodology
package, this-week is the receipts trust-builder. This funnels people who are not ready to
pay $9/mo today but want the next free step.

### 7. Mobile — works
`@media (max-width: 600px)` collapses input-row to column, reduces padding. No fixed widths.
Pixel font scales via `clamp()`. Tested fine.

### 8. Trust signals — adequate
"1,100+ sites roasted · 54 founders following the journey on X" appears twice (hero + bottom
CTA). No founder photo, no logos. Acceptable for a receipt-driven brand.

## Five upgrades — paste-ready

### Upgrade 1 — Headline line-height tighten (1 line)
Change `h1 { ... line-height: 1.5; ... }` → `line-height: 1.35;`
Also add `letter-spacing: 1px;` for pixel-font separation.

### Upgrade 2 — Social-proof anchor (replace the inline `<div>` social bar)
Replace the existing `<!-- Social proof bar -->` div (lines 552–555) with:

```html
<!-- Social proof bar -->
<div style="display:inline-flex;align-items:center;gap:14px;background:rgba(0,255,136,0.06);border:1px solid rgba(0,255,136,0.25);border-radius:6px;padding:12px 20px;margin-bottom:32px;font-size:12px;color:#e8e8e0;flex-wrap:wrap;justify-content:center;max-width:560px;">
  <span style="font-family:'Press Start 2P',monospace;font-size:18px;color:#00ff88;letter-spacing:1px;line-height:1;">1,100+</span>
  <span style="text-align:left;line-height:1.5;">founders roasted &nbsp;·&nbsp; <strong style="color:#00ff88;">5+ min</strong> avg read time</span>
</div>
```

Effect: the **1,100+** becomes a hero token in pixel-font, anchoring trust.

### Upgrade 3 — Form CTA + hint (1 swap)
Bump button to 11px, add accent-dot to hint. Replace `.form-hint` rule:

```css
.form-hint {
  font-size: 12px;
  color: #777;
  margin-top: 14px;
  text-align: left;
}
.form-hint::before {
  content: '●';
  color: #00ff88;
  margin-right: 8px;
  font-size: 9px;
  vertical-align: middle;
}
.roast-btn { font-size: 11px; padding: 16px 26px; }
```

### Upgrade 4 — Live countdown timer in loading state
Add a span inside loading-text and animate it via JS. CSS:

```css
.loading-elapsed { color: #00ff88; font-family: 'Press Start 2P', monospace; font-size: 11px; margin-left: 8px; }
```

HTML edit (line 576) — add an elapsed span:

```html
<div class="loading-text" id="loadingText">Rick is reading your landing page...<span class="loading-elapsed" id="loadingElapsed"></span></div>
```

JS edit — inside `runRoast()`, just before the `loadingInterval = setInterval(...)` block,
add:

```js
let elapsedSec = 0;
const elapsedEl = document.getElementById('loadingElapsed');
const elapsedTimer = setInterval(() => {
  elapsedSec += 1;
  if (elapsedEl) elapsedEl.textContent = elapsedSec + 's';
}, 1000);
```

…and clear it at every `clearInterval(loadingInterval)` site:
```js
clearInterval(elapsedTimer);
```

### Upgrade 5 — Post-roast "next-step" panel (Kit / Pilot / This-Week)
**This is the biggest one.** Insert ABOVE the existing yellow-bordered offer panel
(line 614, before `<div style="margin-top:28px;border-radius:12px;overflow:hidden;border:2px solid #FBBF24;">`):

```html
<!-- Free-path follow-on: Kit / Pilot / This-Week -->
<div style="margin-top:28px;background:#0a0a0a;border:1px solid #2a2a2a;border-radius:12px;padding:24px;">
  <div style="font-family:'Press Start 2P',monospace;font-size:11px;color:#00ff88;letter-spacing:1px;margin-bottom:6px;">NEXT STEP — PICK ONE</div>
  <p style="font-size:13px;color:#999;line-height:1.7;margin-bottom:18px;">Rick just roasted one page. Here's how to keep the momentum — three free or low-cost paths, no commitment:</p>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;">
    <a href="/agents-kit" onclick="trackEvent('cta_clicked',{product:'agents_kit_97',location:'post_roast_freepath'})" style="display:block;background:#111;border:1px solid #FBBF24;border-radius:8px;padding:18px;text-decoration:none;">
      <div style="font-family:'Press Start 2P',monospace;font-size:9px;color:#FBBF24;letter-spacing:1px;margin-bottom:8px;">THE KIT — $97</div>
      <div style="font-size:13px;color:#e8e8e0;line-height:1.6;margin-bottom:10px;">Package this roast methodology so you can run it monthly, on every page.</div>
      <div style="font-size:11px;color:#FBBF24;font-family:'Space Mono',monospace;">Get the AI CEO Kit →</div>
    </a>
    <a href="/pilot" onclick="trackEvent('cta_clicked',{product:'pilot_free',location:'post_roast_freepath'})" style="display:block;background:#111;border:1px solid #00ff88;border-radius:8px;padding:18px;text-decoration:none;position:relative;">
      <div style="position:absolute;top:-9px;right:14px;background:#00ff88;color:#000;font-family:'Press Start 2P',monospace;font-size:7px;padding:3px 7px;border-radius:3px;letter-spacing:0.5px;">FREE</div>
      <div style="font-family:'Press Start 2P',monospace;font-size:9px;color:#00ff88;letter-spacing:1px;margin-bottom:8px;">1-WEEK PILOT</div>
      <div style="font-size:13px;color:#e8e8e0;line-height:1.6;margin-bottom:10px;">Rick runs continuous outreach for your business for one week — receipts, no card.</div>
      <div style="font-size:11px;color:#00ff88;font-family:'Space Mono',monospace;">Start the pilot →</div>
    </a>
    <a href="/this-week" onclick="trackEvent('cta_clicked',{product:'this_week',location:'post_roast_freepath'})" style="display:block;background:#111;border:1px solid #333;border-radius:8px;padding:18px;text-decoration:none;">
      <div style="font-family:'Press Start 2P',monospace;font-size:9px;color:#888;letter-spacing:1px;margin-bottom:8px;">RECEIPTS</div>
      <div style="font-size:13px;color:#e8e8e0;line-height:1.6;margin-bottom:10px;">Rick's auto-published weekly receipts — see what he's actually shipped before you commit.</div>
      <div style="font-size:11px;color:#888;font-family:'Space Mono',monospace;">Read /this-week →</div>
    </a>
  </div>
</div>
```

This sits between the roast result and the existing paid CTA panel — visitors who want the
free path get it; the paid offers stay below for buyers.

## Files
- Source: `/Users/rickthebot/meetrick-site/roast/index.html`
- Audit memo (this file): `/Users/rickthebot/.openclaw/workspace/docs/roast-page-upgrade-2026-05-06.md`

## Out of scope
- A/B testing (no traffic for stat-sig).
- Result-share OG card image — separate task.
- Auto-recharge anything — not Vlad's pattern.
