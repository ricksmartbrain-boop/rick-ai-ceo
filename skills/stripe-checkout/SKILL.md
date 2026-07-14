# Stripe Checkout Skill

Payment infrastructure for all Rick products. Handles product creation, checkout sessions, payment links, and revenue tracking via Stripe.

## Checkout Flow

```
Customer clicks Buy
  -> Redirect to Stripe Checkout (hosted page)
  -> Payment processed by Stripe
  -> Redirect to success page
  -> Webhook fires (invoice.paid)
  -> Update ~/rick-vault/revenue/ snapshot
  -> Send welcome email (if applicable)
```

## Product Naming Convention

All Rick products follow a strict naming scheme:

| Field | Format | Example |
|-------|--------|---------|
| Product name | `rick_{product_name}` | `rick_ai_agent_guide` |
| Price type | `one_time` or `recurring` | `one_time` |
| Payment links | Shareable URL | `https://buy.stripe.com/xxx` |

## Stripe Account

Uses the configured Stripe account for Rick products. All Rick products are tagged with metadata to keep them separate:

```json
{
  "metadata": {
    "agent": "rick",
    "product": "{product_name}"
  }
}
```

Revenue tracking filters by `metadata.agent = rick` to isolate Rick product revenue from Partner Connector revenue.

## Triggers
- **Create product:** New product launch from info-products or product-launcher skill
- **Set up payments:** founder requests payment setup for a product
- **Revenue check:** `/revenue` or nightly review (delegates to revenue-dashboard)

## Commands

### stripe-product.sh

Create, list, and manage Stripe products.

```bash
# Create a new product with price
bash scripts/stripe-product.sh --create --name "ai_agent_guide" --price 2900 --type one_time

# Create a recurring subscription product
bash scripts/stripe-product.sh --create --name "community_membership" --price 9900 --type recurring

# List all Rick products with prices
bash scripts/stripe-product.sh --list

# Generate a shareable payment link
bash scripts/stripe-product.sh --link price_1Abc123

# Archive a product (soft delete)
bash scripts/stripe-product.sh --archive prod_1Abc123

# Sales stats for a specific product
bash scripts/stripe-product.sh --stats prod_1Abc123
```

### stripe-checkout.sh

Create checkout sessions and view revenue.

```bash
# Create a checkout session
bash scripts/stripe-checkout.sh --create --price price_1Abc123 \
  --success-url "https://example.com/thanks" \
  --cancel-url "https://example.com/cancel"

# List recent completed checkout sessions (last 10)
bash scripts/stripe-checkout.sh --recent

# Revenue summary by period
bash scripts/stripe-checkout.sh --revenue --period day
bash scripts/stripe-checkout.sh --revenue --period week
bash scripts/stripe-checkout.sh --revenue --period month
```

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `STRIPE_SECRET_KEY` | Yes | Stripe API key (or use Stripe CLI auth) |

## Revenue Tracking

All Rick product revenue is tracked separately from Partner Connector:

- **Filter:** `metadata.agent = rick` on all Stripe API queries
- **Output:** Revenue snapshots written to `~/rick-vault/revenue/`
- **Integration:** revenue-dashboard skill reads these snapshots for cross-product reporting

## Pricing Strategy

```
Free (lead magnet) -> $29 (guide) -> $99 (course) -> $299 (premium) -> $99/mo (community)
```

Products are created as needed. Payment links are shared via newsletter, social media, and website CTAs.
