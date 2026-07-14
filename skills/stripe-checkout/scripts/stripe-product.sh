#!/usr/bin/env bash
# Stripe product management — create, list, archive products with Rick metadata.
#
# Usage:
#   stripe-product.sh --create --name <name> --price <cents> --type one_time|recurring
#   stripe-product.sh --list
#   stripe-product.sh --link <price_id>
#   stripe-product.sh --archive <product_id>
#   stripe-product.sh --stats <product_id>

set -euo pipefail

# ─── Config ──────────────────────────────────────────────────────────
API_KEY="${STRIPE_SECRET_KEY:-}"
BASE_URL="https://api.stripe.com/v1"

usage() {
    echo "Usage:"
    echo "  stripe-product.sh --create --name <name> --price <cents> --type one_time|recurring"
    echo "  stripe-product.sh --list"
    echo "  stripe-product.sh --link <price_id>"
    echo "  stripe-product.sh --archive <product_id>"
    echo "  stripe-product.sh --stats <product_id>"
    echo ""
    echo "Environment:"
    echo "  STRIPE_SECRET_KEY  Required. Stripe secret API key."
    exit 1
}

check_api_key() {
    if [ -z "$API_KEY" ]; then
        echo "Error: STRIPE_SECRET_KEY is not set."
        echo "Export it or add to your .env file."
        exit 1
    fi
}

stripe_api() {
    local method="$1"
    local endpoint="$2"
    shift 2
    curl -s -X "$method" "$BASE_URL$endpoint" \
        -u "$API_KEY:" \
        "$@"
}

# ─── Commands ────────────────────────────────────────────────────────

cmd_create() {
    local name="" price="" price_type=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --name) name="$2"; shift 2 ;;
            --price) price="$2"; shift 2 ;;
            --type) price_type="$2"; shift 2 ;;
            *) echo "Unknown option: $1"; usage ;;
        esac
    done

    if [ -z "$name" ] || [ -z "$price" ] || [ -z "$price_type" ]; then
        echo "Error: --name, --price, and --type are required for --create."
        usage
    fi

    if [ "$price_type" != "one_time" ] && [ "$price_type" != "recurring" ]; then
        echo "Error: --type must be 'one_time' or 'recurring'."
        exit 1
    fi

    local product_name="rick_${name}"

    echo "Creating product: $product_name"
    echo "  Price: $price cents ($price_type)"
    echo ""

    # Create product
    local product_response
    product_response=$(stripe_api POST /products \
        -d "name=$product_name" \
        -d "metadata[agent]=rick" \
        -d "metadata[product]=$name")

    local product_id
    product_id=$(echo "$product_response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

    if [ -z "$product_id" ]; then
        echo "Error creating product:"
        echo "$product_response" | python3 -m json.tool 2>/dev/null || echo "$product_response"
        exit 1
    fi

    echo "  Product ID: $product_id"

    # Create price
    local price_response
    if [ "$price_type" = "recurring" ]; then
        price_response=$(stripe_api POST /prices \
            -d "product=$product_id" \
            -d "unit_amount=$price" \
            -d "currency=usd" \
            -d "recurring[interval]=month")
    else
        price_response=$(stripe_api POST /prices \
            -d "product=$product_id" \
            -d "unit_amount=$price" \
            -d "currency=usd")
    fi

    local price_id
    price_id=$(echo "$price_response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

    if [ -z "$price_id" ]; then
        echo "Error creating price:"
        echo "$price_response" | python3 -m json.tool 2>/dev/null || echo "$price_response"
        exit 1
    fi

    echo "  Price ID:   $price_id"
    echo ""
    echo "Product created successfully."
    echo "  Next: stripe-product.sh --link $price_id"
}

cmd_list() {
    echo "================================================"
    echo "  Rick Products — Stripe"
    echo "  $(date '+%Y-%m-%d %H:%M')"
    echo "================================================"
    echo ""

    local products_response
    products_response=$(stripe_api GET "/products?limit=100&active=true")

    echo "$products_response" | python3 -c "
import sys, json

data = json.load(sys.stdin)
products = [p for p in data.get('data', []) if p.get('metadata', {}).get('agent') == 'rick']

if not products:
    print('  No Rick products found.')
    sys.exit(0)

print(f'  Found {len(products)} product(s)')
print('')

for p in products:
    name = p['name']
    pid = p['id']
    meta_product = p.get('metadata', {}).get('product', '?')
    active = 'Active' if p.get('active') else 'Archived'
    print(f'  {name}')
    print(f'    ID:      {pid}')
    print(f'    Product: {meta_product}')
    print(f'    Status:  {active}')
    print('')
" 2>/dev/null

    echo "================================================"
}

cmd_link() {
    local price_id="$1"

    if [ -z "$price_id" ]; then
        echo "Error: Price ID required."
        echo "Usage: stripe-product.sh --link <price_id>"
        exit 1
    fi

    echo "Creating payment link for price: $price_id"

    local response
    response=$(stripe_api POST /payment_links \
        -d "line_items[0][price]=$price_id" \
        -d "line_items[0][quantity]=1")

    local link_url
    link_url=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))" 2>/dev/null)

    if [ -z "$link_url" ]; then
        echo "Error creating payment link:"
        echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"
        exit 1
    fi

    echo ""
    echo "  Payment Link: $link_url"
    echo ""
    echo "Share this link via newsletter, social media, or website."
}

cmd_archive() {
    local product_id="$1"

    if [ -z "$product_id" ]; then
        echo "Error: Product ID required."
        echo "Usage: stripe-product.sh --archive <product_id>"
        exit 1
    fi

    echo "Archiving product: $product_id"

    local response
    response=$(stripe_api POST "/products/$product_id" \
        -d "active=false")

    local archived_name
    archived_name=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('name',''))" 2>/dev/null)

    echo "  Archived: $archived_name ($product_id)"
}

cmd_stats() {
    local product_id="$1"

    if [ -z "$product_id" ]; then
        echo "Error: Product ID required."
        echo "Usage: stripe-product.sh --stats <product_id>"
        exit 1
    fi

    echo "================================================"
    echo "  Product Stats: $product_id"
    echo "  $(date '+%Y-%m-%d %H:%M')"
    echo "================================================"
    echo ""

    # Get completed checkout sessions for this product
    local charges_response
    charges_response=$(stripe_api GET "/charges?limit=100")

    # Get invoices that include this product
    local invoices_response
    invoices_response=$(stripe_api GET "/invoices?limit=100&status=paid")

    # Parse with python
    python3 -c "
import sys, json

# We'll count payment intents related to this product
product_id = '$product_id'

# For now, show basic stats from charges
print('  Note: Detailed per-product stats require Payment Intent')
print('  metadata filtering. Use Stripe Dashboard for full details.')
print('')
print(f'  Stripe Dashboard: https://dashboard.stripe.com/products/{product_id}')
print('')
" 2>/dev/null

    echo "================================================"
}

# ─── Main ────────────────────────────────────────────────────────────

if [ $# -eq 0 ]; then
    usage
fi

check_api_key

case "$1" in
    --create)
        shift
        cmd_create "$@"
        ;;
    --list)
        cmd_list
        ;;
    --link)
        shift
        cmd_link "${1:-}"
        ;;
    --archive)
        shift
        cmd_archive "${1:-}"
        ;;
    --stats)
        shift
        cmd_stats "${1:-}"
        ;;
    -h|--help)
        usage
        ;;
    *)
        echo "Unknown command: $1"
        usage
        ;;
esac
