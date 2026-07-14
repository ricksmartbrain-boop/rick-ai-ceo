#!/usr/bin/env bash
# Generate a landing page from templates — replaces placeholders and outputs a ready page.
#
# Usage:
#   create-landing-page.sh --product <name> --headline <text> --cta <text> \
#     [--payment-link <url>] [--stripe-price <id>] --template minimal|sales|waitlist [--output <dir>] \
#     [--waitlist-api <url>]

set -euo pipefail

PRODUCT=""
HEADLINE=""
CTA=""
STRIPE_PRICE=""
PAYMENT_LINK=""
TEMPLATE=""
OUTPUT_DIR=""
WAITLIST_API=""
TEMPLATE_DIR="$(cd "$(dirname "$0")/../templates" && pwd)"

is_real_public_url() {
    local url="$1"
    local host

    [[ "$url" =~ ^https?:// ]] || return 1
    host="${url#*://}"
    host="${host%%/*}"
    host="${host%%:*}"
    host="$(echo "$host" | tr '[:upper:]' '[:lower:]')"

    [[ -n "$host" ]] || return 1
    [[ "$host" == *.* ]] || return 1
    case "$host" in
        localhost|127.0.0.1|0.0.0.0|example.com|example.org|example.net)
            return 1
            ;;
    esac
    case "$host" in
        *.invalid|*.example|*.test|*.local|*.example.com|*.example.org|*.example.net)
            return 1
            ;;
    esac
    return 0
}

usage() {
    cat <<EOF
Landing Page Generator

Usage:
  create-landing-page.sh [flags]

Flags:
  --product <name>         Product name (required)
  --headline <text>        Hero headline (required)
  --cta <text>             Call-to-action button text (required)
  --stripe-price <id>      Stripe price ID (only for placeholder app routes)
  --payment-link <url>     Stripe payment link or app checkout URL
  --template <type>        Template: minimal | sales | waitlist (required)
  --output <dir>           Output directory (default: stdout for page.tsx)
  --waitlist-api <url>     Waitlist API endpoint (waitlist template only)

Examples:
  create-landing-page.sh \\
    --product "AI Lead Scorer" \\
    --headline "Score Every Lead in Seconds" \\
    --cta "Start Free Trial" \\
    --stripe-price price_1Abc \\
    --template minimal

  create-landing-page.sh \\
    --product "PartnerFlow" \\
    --headline "The Marketplace is Coming" \\
    --cta "Join the Waitlist" \\
    --template waitlist \\
    --waitlist-api "https://api.example.com/waitlist" \\
    --output ./partnerflow-site
EOF
    exit 0
}

[[ $# -eq 0 ]] && usage

while [[ $# -gt 0 ]]; do
    case $1 in
        --product)
            PRODUCT="${2:-}"
            [[ -z "$PRODUCT" ]] && { echo "Error: --product requires a value"; exit 1; }
            shift 2
            ;;
        --headline)
            HEADLINE="${2:-}"
            [[ -z "$HEADLINE" ]] && { echo "Error: --headline requires a value"; exit 1; }
            shift 2
            ;;
        --cta)
            CTA="${2:-}"
            [[ -z "$CTA" ]] && { echo "Error: --cta requires a value"; exit 1; }
            shift 2
            ;;
        --stripe-price)
            STRIPE_PRICE="${2:-}"
            [[ -z "$STRIPE_PRICE" ]] && { echo "Error: --stripe-price requires a value"; exit 1; }
            shift 2
            ;;
        --payment-link)
            PAYMENT_LINK="${2:-}"
            [[ -z "$PAYMENT_LINK" ]] && { echo "Error: --payment-link requires a value"; exit 1; }
            shift 2
            ;;
        --template)
            TEMPLATE="${2:-}"
            [[ -z "$TEMPLATE" ]] && { echo "Error: --template requires a value"; exit 1; }
            shift 2
            ;;
        --output)
            OUTPUT_DIR="${2:-}"
            [[ -z "$OUTPUT_DIR" ]] && { echo "Error: --output requires a value"; exit 1; }
            shift 2
            ;;
        --waitlist-api)
            WAITLIST_API="${2:-}"
            [[ -z "$WAITLIST_API" ]] && { echo "Error: --waitlist-api requires a value"; exit 1; }
            shift 2
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            echo "Run with --help for usage."
            exit 1
            ;;
    esac
done

# Validate required flags
[[ -z "$PRODUCT" ]] && { echo "Error: --product is required"; exit 1; }
[[ -z "$HEADLINE" ]] && { echo "Error: --headline is required"; exit 1; }
[[ -z "$CTA" ]] && { echo "Error: --cta is required"; exit 1; }
[[ -z "$TEMPLATE" ]] && { echo "Error: --template is required"; exit 1; }

# Resolve template directory
case $TEMPLATE in
    minimal) TMPL_DIR="$TEMPLATE_DIR/landing-minimal" ;;
    sales)   TMPL_DIR="$TEMPLATE_DIR/landing-sales" ;;
    waitlist) TMPL_DIR="$TEMPLATE_DIR/landing-waitlist" ;;
    *)
        echo "Error: Unknown template '$TEMPLATE'. Choose: minimal | sales | waitlist"
        exit 1
        ;;
esac

if [[ ! -f "$TMPL_DIR/page.tsx" ]]; then
    echo "Error: Template file not found at $TMPL_DIR/page.tsx"
    exit 1
fi

# Build checkout URL safely
CHECKOUT_URL=""
if [[ -n "$PAYMENT_LINK" ]]; then
    if ! is_real_public_url "$PAYMENT_LINK"; then
        echo "Error: --payment-link must be a real public URL, got: $PAYMENT_LINK" >&2
        exit 1
    fi
    CHECKOUT_URL="$PAYMENT_LINK"
elif [[ -n "$STRIPE_PRICE" ]]; then
    echo "Warning: Stripe price ids do not map directly to checkout URLs. Provide --payment-link for a real CTA." >&2
fi

# Validate launch path
if [[ "$TEMPLATE" == "sales" && -z "$CHECKOUT_URL" ]]; then
    echo "Error: sales template requires --payment-link with a real public checkout URL." >&2
    exit 1
fi

if [[ "$TEMPLATE" == "waitlist" ]]; then
    if [[ -z "$WAITLIST_API" ]]; then
        echo "Error: waitlist template requires --waitlist-api with a real public endpoint." >&2
        exit 1
    fi
    if ! is_real_public_url "$WAITLIST_API"; then
        echo "Error: --waitlist-api must be a real public URL, got: $WAITLIST_API" >&2
        exit 1
    fi
fi

# Perform replacements
PAGE_CONTENT=$(cat "$TMPL_DIR/page.tsx")
PAGE_CONTENT="${PAGE_CONTENT//\{\{PRODUCT_NAME\}\}/$PRODUCT}"
PAGE_CONTENT="${PAGE_CONTENT//\{\{HEADLINE\}\}/$HEADLINE}"
PAGE_CONTENT="${PAGE_CONTENT//\{\{CTA_TEXT\}\}/$CTA}"
PAGE_CONTENT="${PAGE_CONTENT//\{\{STRIPE_CHECKOUT_URL\}\}/$CHECKOUT_URL}"
PAGE_CONTENT="${PAGE_CONTENT//\{\{WAITLIST_API_URL\}\}/$WAITLIST_API}"

if [[ -n "$OUTPUT_DIR" ]]; then
    # Write to output directory
    mkdir -p "$OUTPUT_DIR/app"
    echo "$PAGE_CONTENT" > "$OUTPUT_DIR/app/page.tsx"

    # Copy package.json if it exists
    if [[ -f "$TMPL_DIR/package.json" ]]; then
        cp "$TMPL_DIR/package.json" "$OUTPUT_DIR/package.json"
    fi

    echo "Landing page created at $OUTPUT_DIR/app/page.tsx" >&2
    echo "" >&2
    echo "Next steps:" >&2
    echo "  cd $OUTPUT_DIR" >&2
    echo "  npm install" >&2
    echo "  npm run dev" >&2
else
    # Output to stdout
    echo "$PAGE_CONTENT"
fi
