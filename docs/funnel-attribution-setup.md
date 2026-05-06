# Funnel Attribution — One-Time Setup

`scripts/funnel-attribution.py` reports three numbers each week to identify
*which step* leaks. Two of those numbers are read-only, no setup. One needs
a 5-minute manual config in two places.

## What's already wired

- **Newsletter CTR (#1)** — read from Resend `/emails` API via existing
  `RESEND_API_KEY`. No setup needed. Values become non-zero after the next
  Sunday `rick-roundup-weekly.py` send.
- **Stripe-init → completion (#3)** — read from Stripe `/v1/events` API.
  Requires `STRIPE_SECRET_KEY` in the local env (or runs from Railway).
- **UTM tagging** — `runtime/newsletter_drafter.py` and
  `scripts/rick-roundup-weekly.py` now stamp every `meetrick.ai` link with
  `utm_source=newsletter & utm_medium=email & utm_campaign=issue-NNN` (or
  `roundup-YYYY-wWW` for the Sunday broadcast). Pre-existing UTM params on
  hand-crafted URLs WIN — drafts already containing UTMs are untouched.

## What's missing — pick one of two paths

### Path A (recommended): Stripe webhook log on Railway

The Railway-hosted `meetrick-api` already has a Stripe webhook receiver at
`POST /api/v1/stripe/webhook` (see `~/meetrick/api/src/routes/stripe-webhook.js`).
It logs every event to stdout in JSON (Railway captures these). For
`funnel-attribution.py` to consume them locally without the Stripe API,
add a single fire-and-forget append to a log file mirrored to the local
vault. You can skip this step entirely if `STRIPE_SECRET_KEY` is in
`~/clawd/config/rick.env` — the script falls back to Stripe `/v1/events`.

**5-step Stripe dashboard config (only if webhook is NOT yet registered):**

1. Stripe dashboard → Developers → Webhooks → "Add endpoint"
2. URL: `https://api.meetrick.ai/api/v1/stripe/webhook`
3. Events to listen for (select 6):
   - `checkout.session.created`  ← needed for #2 numerator
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
   - `invoice.payment_succeeded`
4. Reveal the signing secret, then on Railway: project → Variables →
   add `STRIPE_WEBHOOK_SECRET=whsec_...` and redeploy.
5. Test: Stripe dashboard → "Send test webhook" → `checkout.session.created`.
   Confirm Railway logs show `[ISO timestamp] Stripe webhook: checkout.session.created`.

After step 5, `funnel-attribution.py --summary` will populate #2 numerator
on the next run.

### Path B (only if you want true per-link CTR): Resend webhook

Resend's `/emails` API gives per-recipient `last_event` only — that's
what `funnel-attribution.py` uses today as a proxy ("recipient clicked
at least one link"). For true per-link click counts, register a Resend
webhook:

1. Resend dashboard → Webhooks → "Add webhook"
2. URL: `https://api.meetrick.ai/api/v1/resend/webhook` *(needs to be
   created — see "Pricing-page tracker" below for a sibling endpoint
   pattern)*
3. Events: `email.sent`, `email.delivered`, `email.opened`, `email.clicked`,
   `email.bounced`
4. Save webhook secret to Railway env as `RESEND_WEBHOOK_SECRET`
5. Test: Resend "Send test event" → confirm Railway logs show event

Until Path B is wired, the recipient-level proxy CTR from Resend's API
is what we get — flagged as such in the script's `method` field.

## What's needed for #2 denominator (pricing-page sessions)

Number 2 is `(checkout.session.created / pricing-page sessions)`. The
numerator is wired (Stripe Events). The denominator requires a
landing-page tracker — currently NONE exists. Two options:

**Option 1 (lightest): Cloudflare Workers analytics**
The `meetrick-site` is on Cloudflare Pages. Enable Cloudflare Web Analytics,
then export weekly via the CF API into `~/rick-vault/operations/landing-pings.jsonl`
with rows `{ts, path, ref, utm_source, utm_medium, utm_campaign}`. The
attribution script already looks for this file path.

**Option 2: 1-pixel ping endpoint**
Add a `<script>` snippet to `~/meetrick-site/pricing/index.html` that POSTs
`{path, utm_*, ref, ts}` to a new `https://api.meetrick.ai/api/v1/landing/ping`
endpoint. The endpoint appends rows to a Postgres table; a daily polling
script in `scripts/landing-poll.py` would mirror them locally to
`~/rick-vault/operations/landing-pings.jsonl`. Same downstream contract
as Option 1.

Until either lands, #2 reports `null` with `status: "no_landing_tracker"`.

## Verification

```bash
# Run once locally
bash -c 'set -a; source ~/clawd/config/rick.env; set +a; \
  python3 ~/.openclaw/workspace/scripts/funnel-attribution.py --summary'

# Check the snapshot was appended
ls -la ~/rick-vault/operations/funnel-attribution-*.jsonl

# Confirm the weekly roundup picks it up
bash -c 'set -a; source ~/clawd/config/rick.env; set +a; \
  python3 ~/.openclaw/workspace/scripts/rick-roundup-weekly.py --dry-run' \
  | grep -A4 "3-number funnel"
```

## Why this matters

Today: "did MRR move?" is the only signal — 30-day lag. Three weeks of
flat MRR could mean (a) newsletter isn't getting clicks, (b) clicks
aren't reaching pricing, (c) pricing isn't converting to checkout, (d)
checkouts are getting abandoned. Without the three numbers, fixing the
wrong stage is the default outcome. With them, every Sunday roundup
shows where the leak is — and a messaging or pricing change has a
falsifiable target by the next Sunday.
