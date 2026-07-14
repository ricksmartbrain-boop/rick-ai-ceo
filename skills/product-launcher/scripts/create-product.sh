#!/usr/bin/env bash
# Create a new product with Stripe integration and project scaffolding.
#
# Usage:
#   create-product.sh --type guide --name "AI Agent Playbook" --price 29
#   create-product.sh --type course --name "Building AI Agents" --price 99
#   create-product.sh --type template --name "SaaS Launch Kit" --price 19
#   create-product.sh --type tool --name "Revenue Tracker" --price 29

set -euo pipefail

RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
RICK_PUBLIC_AUTHOR="${RICK_PUBLIC_AUTHOR:-Rick}"
RICK_BRAND_BLURB="${RICK_BRAND_BLURB:-AI-operated founder brand building autonomous revenue systems in public.}"
TYPE=""
NAME=""
PRICE=""

usage() {
    echo "Usage: create-product.sh --type <type> --name <name> --price <amount>"
    echo ""
    echo "Options:"
    echo "  --type   Product type: guide | course | template | tool"
    echo "  --name   Product name (e.g., 'AI Agent Playbook')"
    echo "  --price  Price in USD (e.g., 29 -- converts to cents for Stripe)"
    echo ""
    echo "Examples:"
    echo "  create-product.sh --type guide --name 'AI Agent Playbook' --price 29"
    echo "  create-product.sh --type course --name 'Building AI Agents' --price 99"
    echo "  create-product.sh --type template --name 'SaaS Launch Kit' --price 19"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --type) TYPE="$2"; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --price) PRICE="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [ -z "$TYPE" ] || [ -z "$NAME" ] || [ -z "$PRICE" ]; then
    echo "Error: --type, --name, and --price are all required."
    echo ""
    usage
fi

# Validate type
case $TYPE in
    guide|course|template|tool) ;;
    *) echo "Error: Invalid type '$TYPE'. Must be: guide, course, template, or tool."; exit 1 ;;
esac

# Validate price is a number
if ! [[ "$PRICE" =~ ^[0-9]+$ ]]; then
    echo "Error: Price must be a whole number in USD (e.g., 29)."
    exit 1
fi

SLUG=$(echo "$NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')
PRICE_CENTS=$((PRICE * 100))
DATE=$(date '+%Y-%m-%d')
TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
PROJECT_DIR="$RICK_DATA_ROOT/projects/$SLUG"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==========================================="
echo "  Product Creator"
echo "  $TIMESTAMP"
echo "==========================================="
echo ""
echo "Name:   $NAME"
echo "Type:   $TYPE"
echo "Price:  \$$PRICE (\$$PRICE_CENTS cents for Stripe)"
echo "Slug:   $SLUG"
echo ""

# --- 1. Create project directory structure ---

echo "--- Creating project directory ---"
mkdir -p "$PROJECT_DIR/content"
mkdir -p "$PROJECT_DIR/assets"
mkdir -p "$PROJECT_DIR/marketing"
mkdir -p "$PROJECT_DIR/emails"
echo "Created: $PROJECT_DIR/"
echo ""

# --- 2. Create Stripe product + price ---

echo "--- Stripe Product ---"
if command -v stripe &> /dev/null; then
    echo "Creating Stripe product..."
    STRIPE_PRODUCT=$(stripe products create \
        --name "$NAME" \
        --description "Digital $TYPE: $NAME" \
        --metadata "type=$TYPE" \
        --metadata "slug=$SLUG" \
        --metadata "created=$DATE" \
        2>&1) || true

    if echo "$STRIPE_PRODUCT" | grep -q '"id"'; then
        PRODUCT_ID=$(echo "$STRIPE_PRODUCT" | grep '"id"' | head -1 | sed 's/.*"id": "\(.*\)".*/\1/')
        echo "Product created: $PRODUCT_ID"

        echo "Creating Stripe price..."
        STRIPE_PRICE=$(stripe prices create \
            --product "$PRODUCT_ID" \
            --unit-amount "$PRICE_CENTS" \
            --currency usd \
            2>&1) || true

        if echo "$STRIPE_PRICE" | grep -q '"id"'; then
            PRICE_ID=$(echo "$STRIPE_PRICE" | grep '"id"' | head -1 | sed 's/.*"id": "\(.*\)".*/\1/')
            echo "Price created: $PRICE_ID"
            PAYMENT_LINK_URL=""

            if [[ -n "${STRIPE_SECRET_KEY:-}" ]]; then
                LINK_OUTPUT=$(bash "$SCRIPT_DIR/../../stripe-checkout/scripts/stripe-product.sh" --link "$PRICE_ID" 2>/dev/null || true)
                PAYMENT_LINK_URL=$(echo "$LINK_OUTPUT" | sed -n 's/.*Payment Link: //p' | head -1)
                if [[ -n "$PAYMENT_LINK_URL" ]]; then
                    echo "Payment link created: $PAYMENT_LINK_URL"
                fi
            fi

            # Save Stripe IDs
            cat > "$PROJECT_DIR/stripe-product.json" <<STRIPE
{
  "product_id": "$PRODUCT_ID",
  "price_id": "$PRICE_ID",
  "payment_link_url": "$PAYMENT_LINK_URL",
  "price_usd": $PRICE,
  "price_cents": $PRICE_CENTS,
  "type": "$TYPE",
  "name": "$NAME",
  "created": "$DATE",
  "status": "$(if [[ -n "$PAYMENT_LINK_URL" ]]; then echo "checkout-ready"; else echo "product-ready"; fi)",
  "next_step": "$(if [[ -n "$PAYMENT_LINK_URL" ]]; then echo "Proceed to landing page and launch prep."; else echo "Create a real payment link or configure a real waitlist API before launch."; fi)"
}
STRIPE
            echo "Stripe config saved: $PROJECT_DIR/stripe-product.json"
        else
            echo "Warning: Could not create Stripe price. Create manually."
            echo "$STRIPE_PRICE"
        fi
    else
        echo "Warning: Could not create Stripe product. Create manually."
        echo "  stripe products create --name '$NAME' --description 'Digital $TYPE: $NAME'"
        echo "  stripe prices create --product <PRODUCT_ID> --unit-amount $PRICE_CENTS --currency usd"
    fi
else
    echo "Stripe CLI not installed. Install: brew install stripe/stripe-cli/stripe"
    echo ""
    echo "Manual steps:"
    echo "  1. Create product in Stripe dashboard"
    echo "  2. Set price to \$$PRICE"
    echo "  3. Save product/price IDs to $PROJECT_DIR/stripe-product.json"

    # Create placeholder
    cat > "$PROJECT_DIR/stripe-product.json" <<STRIPE
{
  "product_id": "",
  "price_id": "",
  "payment_link_url": "",
  "price_usd": $PRICE,
  "price_cents": $PRICE_CENTS,
  "type": "$TYPE",
  "name": "$NAME",
  "created": "$DATE",
  "status": "manual-required",
  "next_step": "Create the product and price in Stripe, then replace the empty ids."
}
STRIPE
fi
echo ""

# --- 3. Create landing page scaffold ---

echo "--- Landing Page Scaffold ---"

case $TYPE in
    guide)
        PRODUCT_LABEL="PDF Guide"
        PRODUCT_DESC="A comprehensive guide covering everything you need to know about $NAME."
        CTA_TEXT="Get the Guide -- \$$PRICE"
        ;;
    course)
        PRODUCT_LABEL="Online Course"
        PRODUCT_DESC="A hands-on course that teaches you $NAME step by step."
        CTA_TEXT="Enroll Now -- \$$PRICE"
        ;;
    template)
        PRODUCT_LABEL="Template Pack"
        PRODUCT_DESC="Ready-to-use templates for $NAME. Copy, customize, and ship."
        CTA_TEXT="Get the Templates -- \$$PRICE"
        ;;
    tool)
        PRODUCT_LABEL="SaaS Tool"
        PRODUCT_DESC="A powerful tool for $NAME that saves you hours every week."
        CTA_TEXT="Start Free Trial"
        ;;
esac

cat > "$PROJECT_DIR/marketing/landing-page.md" <<PAGE
# $NAME

## $PRODUCT_LABEL -- \$$PRICE

$PRODUCT_DESC

### What You Get

- [Benefit 1: specific outcome]
- [Benefit 2: specific outcome]
- [Benefit 3: specific outcome]

### Who This Is For

- [Audience 1: role or situation]
- [Audience 2: role or situation]
- [Audience 3: role or situation]

### What's Inside

[Detailed breakdown of contents -- chapters, modules, templates, etc.]

### Social Proof

> "[Testimonial from beta user or early customer]"
> -- Name, Title

### FAQ

**Q: Is this right for me?**
A: [Answer]

**Q: What format is it in?**
A: [Answer]

**Q: Is there a refund policy?**
A: Yes, 30-day money-back guarantee. No questions asked.

### CTA

$CTA_TEXT

---

*Built by $RICK_PUBLIC_AUTHOR -- $RICK_BRAND_BLURB*
PAGE

echo "Landing page draft: $PROJECT_DIR/marketing/landing-page.md"
echo ""

# --- 4. Create email sequence drafts ---

echo "--- Email Welcome Sequence ---"

cat > "$PROJECT_DIR/emails/welcome-1-delivery.md" <<EMAIL1
# Email 1: Delivery + Quick Win

**Subject:** Your copy of $NAME is ready
**Send:** Immediately after purchase

---

Hi {{first_name}},

Thanks for grabbing $NAME!

Here's your access: [LINK]

**Quick win to get started:**
[One specific action they can take in the next 5 minutes]

If you have any questions, just reply to this email.

-- $RICK_PUBLIC_AUTHOR
EMAIL1

cat > "$PROJECT_DIR/emails/welcome-2-value.md" <<EMAIL2
# Email 2: Key Insight

**Subject:** The #1 mistake with [topic]
**Send:** Day 2 after purchase

---

Hi {{first_name}},

Now that you've had $NAME for a day, here's the insight that most people miss:

[Share a key concept or common mistake]

**Action step:** [Specific thing to try today]

How's it going so far? Reply and let me know.

-- $RICK_PUBLIC_AUTHOR
EMAIL2

cat > "$PROJECT_DIR/emails/welcome-3-feedback.md" <<EMAIL3
# Email 3: Feedback + Social Proof

**Subject:** Quick question about $NAME
**Send:** Day 5 after purchase

---

Hi {{first_name}},

I'd love to hear how $NAME is working for you.

Could you take 30 seconds to answer one question?

**What was the most useful part so far?**

Just hit reply -- I read every response.

PS: If you're finding it valuable, a quick testimonial would mean the world. Just reply with a sentence or two about your experience.

-- $RICK_PUBLIC_AUTHOR
EMAIL3

echo "Email sequence: $PROJECT_DIR/emails/"
echo "  - welcome-1-delivery.md"
echo "  - welcome-2-value.md"
echo "  - welcome-3-feedback.md"
echo ""

# --- 5. Create social announcement drafts ---

echo "--- Social Announcement Drafts ---"

cat > "$PROJECT_DIR/marketing/social-launch-linkedin.md" <<LINKEDIN
# LinkedIn Launch Post

I just launched $NAME.

Here's why I built it:

[Problem you noticed in the market]

[What you did about it]

[Key result or insight]

It's a $PRODUCT_LABEL priced at \$$PRICE.

If you're [target audience], this will save you [time/money/effort].

Link in comments.

---

*Hashtags: #[relevant] #[relevant] #buildinpublic*
LINKEDIN

cat > "$PROJECT_DIR/marketing/social-launch-twitter.md" <<TWITTER
# X/Twitter Launch Thread

Tweet 1:
I just shipped $NAME.

Here's the story behind it (and what I learned building it):

Tweet 2:
The problem: [What you noticed]

Tweet 3:
The solution: [What you built]

Tweet 4:
What's inside:
- [Feature/chapter 1]
- [Feature/chapter 2]
- [Feature/chapter 3]

Tweet 5:
It's \$$PRICE. Link: [URL]

If you're [target audience], this was built for you.
TWITTER

echo "Social drafts: $PROJECT_DIR/marketing/"
echo "  - social-launch-linkedin.md"
echo "  - social-launch-twitter.md"
echo ""

# --- 6. Create launch plan if doesn't exist ---

if [ ! -f "$PROJECT_DIR/launch-plan.md" ]; then
    echo "--- Generating Launch Plan ---"
    bash "$SCRIPT_DIR/product-launch.sh" --plan "$SLUG" 2>/dev/null || true
fi

echo "==========================================="
echo ""
echo "Product scaffolding complete!"
echo ""
echo "Project directory: $PROJECT_DIR"
echo ""
echo "Next steps:"
echo "  1. Fill in landing page copy: $PROJECT_DIR/marketing/landing-page.md"
echo "  2. Write product content: $PROJECT_DIR/content/"
echo "  3. Customize email sequence: $PROJECT_DIR/emails/"
echo "  4. Build landing page: website-builder skill"
echo "  5. Launch: product-launch.sh --launch $SLUG"
echo ""
echo "==========================================="
