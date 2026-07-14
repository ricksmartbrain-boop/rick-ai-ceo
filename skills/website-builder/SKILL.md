# Website Builder Skill

Deploy websites and landing pages for products — scaffold, customize, ship to Vercel.

## Triggers

- "create website", "build landing page", "deploy site", "launch page"
- Nightly review: if a product exists without a landing page, prompt to create one
- When a new info product or SaaS reaches "ready" stage

## Conventions

- **Framework:** Next.js 14+ App Router with Tailwind CSS
- **Hosting:** Vercel (vercel CLI for deploys)
- **Payments:** Stripe Checkout (redirect mode, no embedded forms)
- **Analytics:** Plausible (privacy-friendly, no cookie banner needed)
- **Domain:** Custom domains via Vercel DNS or external registrar

## Deployment Flow

```
1. Scaffold from template    → deploy-site.sh --init <name>
2. Customize with AI         → edit page.tsx, swap placeholders
3. Deploy to Vercel          → deploy-site.sh --deploy <path>
4. Wire custom domain        → vercel domains add <domain>
5. Test checkout flow        → verify Stripe redirect works
6. Log to ~/rick-vault/projects/   → update project file with URL + status
```

## Templates

| Template | Use Case | Key Sections |
|----------|----------|--------------|
| `landing-minimal` | Simple product launch, one CTA | Hero, single CTA button |
| `landing-sales` | Full sales page, multiple sections | Hero, features, testimonials, pricing, FAQ, footer CTA |
| `landing-waitlist` | Pre-launch email capture | Hero, email form, value props, subscriber count |

## Commands

### deploy-site.sh

Deploy and manage Vercel-hosted sites.

```bash
# Scaffold a new Next.js project from template
bash scripts/deploy-site.sh --init my-product

# Deploy to Vercel (production)
bash scripts/deploy-site.sh --deploy ./my-product

# Deploy preview (non-production)
bash scripts/deploy-site.sh --deploy ./my-product --preview

# Check deployment status
bash scripts/deploy-site.sh --status my-product

# List custom domains
bash scripts/deploy-site.sh --domains my-product

# Tail recent deployment logs
bash scripts/deploy-site.sh --logs my-product
```

### create-landing-page.sh

Generate a landing page from templates.

```bash
# Minimal landing page
bash scripts/create-landing-page.sh \
  --product "AI Lead Scorer" \
  --headline "Score Every Lead in Seconds" \
  --cta "Start Free Trial" \
  --stripe-price price_1234 \
  --template minimal

# Full sales page
bash scripts/create-landing-page.sh \
  --product "B2B Outbound Mastery" \
  --headline "Close More Deals with Cold Email" \
  --cta "Get the Course" \
  --stripe-price price_5678 \
  --template sales \
  --output ./b2b-course

# Waitlist page
bash scripts/create-landing-page.sh \
  --product "PartnerFlow" \
  --headline "The Partner Marketplace is Coming" \
  --cta "Join the Waitlist" \
  --template waitlist \
  --waitlist-api "https://api.example.com/waitlist"
```

## Environment Variables

```bash
VERCEL_TOKEN          # Vercel API token for CLI auth
VERCEL_ORG_ID         # Vercel team/org ID
STRIPE_PUBLISHABLE_KEY  # Stripe publishable key (for checkout redirects)
PLAUSIBLE_DOMAIN      # Plausible analytics domain
```

## Post-Deploy Checklist

- [ ] Page loads on custom domain (HTTPS)
- [ ] Stripe Checkout redirect works (test mode first)
- [ ] Plausible tracking fires on page load
- [ ] Mobile responsive (test 375px, 768px, 1024px)
- [ ] Meta tags set (title, description, OG image)
- [ ] Project file updated in ~/rick-vault/projects/
