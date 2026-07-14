#!/usr/bin/env bash
# Stripe checkout sessions — create sessions, list recent, revenue summary.
#
# Usage:
#   stripe-checkout.sh --create --price <id> --success-url <url> --cancel-url <url>
#   stripe-checkout.sh --recent
#   stripe-checkout.sh --revenue --period day|week|month

set -euo pipefail

# ─── Config ──────────────────────────────────────────────────────────
API_KEY="${STRIPE_SECRET_KEY:-}"
BASE_URL="https://api.stripe.com/v1"

usage() {
    echo "Usage:"
    echo "  stripe-checkout.sh --create --price <id> --success-url <url> --cancel-url <url>"
    echo "  stripe-checkout.sh --recent"
    echo "  stripe-checkout.sh --revenue --period day|week|month"
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
    local price_id="" success_url="" cancel_url=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --price) price_id="$2"; shift 2 ;;
            --success-url) success_url="$2"; shift 2 ;;
            --cancel-url) cancel_url="$2"; shift 2 ;;
            *) echo "Unknown option: $1"; usage ;;
        esac
    done

    if [ -z "$price_id" ] || [ -z "$success_url" ] || [ -z "$cancel_url" ]; then
        echo "Error: --price, --success-url, and --cancel-url are required."
        usage
    fi

    echo "Creating checkout session..."
    echo "  Price: $price_id"
    echo "  Success URL: $success_url"
    echo "  Cancel URL: $cancel_url"
    echo ""

    local response
    response=$(stripe_api POST /checkout/sessions \
        -d "line_items[0][price]=$price_id" \
        -d "line_items[0][quantity]=1" \
        -d "mode=payment" \
        -d "success_url=$success_url" \
        -d "cancel_url=$cancel_url" \
        -d "metadata[agent]=rick")

    local session_url session_id
    session_url=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))" 2>/dev/null)
    session_id=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

    if [ -z "$session_url" ]; then
        echo "Error creating checkout session:"
        echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"
        exit 1
    fi

    echo "  Session ID:  $session_id"
    echo "  Checkout URL: $session_url"
    echo ""
    echo "Redirect customer to the checkout URL."
}

cmd_recent() {
    echo "================================================"
    echo "  Recent Checkout Sessions"
    echo "  $(date '+%Y-%m-%d %H:%M')"
    echo "================================================"
    echo ""

    local response
    response=$(stripe_api GET "/checkout/sessions?limit=10&status=complete")

    echo "$response" | python3 -c "
import sys, json
from datetime import datetime

data = json.load(sys.stdin)
sessions = data.get('data', [])

# Filter to Rick sessions (those with agent=rick metadata)
rick_sessions = [s for s in sessions if s.get('metadata', {}).get('agent') == 'rick']
other_sessions = [s for s in sessions if s.get('metadata', {}).get('agent') != 'rick']

if rick_sessions:
    print(f'  Rick Sessions: {len(rick_sessions)}')
    print('')
    for s in rick_sessions:
        sid = s['id'][:20] + '...'
        amount = s.get('amount_total', 0) / 100
        created = datetime.fromtimestamp(s['created']).strftime('%Y-%m-%d %H:%M')
        email = s.get('customer_details', {}).get('email', 'N/A')
        status = s.get('payment_status', '?')
        print(f'  {created}  \${amount:>8.2f}  {status:>8}  {email}')
    print('')
else:
    print('  No Rick checkout sessions found.')
    print('')

total_shown = len(rick_sessions)
print(f'  Showing {total_shown} Rick session(s) out of {len(sessions)} total')
" 2>/dev/null

    echo ""
    echo "================================================"
}

cmd_revenue() {
    local period=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --period) period="$2"; shift 2 ;;
            *) echo "Unknown option: $1"; usage ;;
        esac
    done

    if [ -z "$period" ]; then
        echo "Error: --period is required (day, week, month)."
        usage
    fi

    echo "================================================"
    echo "  Rick Revenue — ${period}"
    echo "  $(date '+%Y-%m-%d %H:%M')"
    echo "================================================"
    echo ""

    # Calculate date range
    local created_gte
    case "$period" in
        day)
            created_gte=$(date -v-1d +%s 2>/dev/null || date -d '1 day ago' +%s)
            ;;
        week)
            created_gte=$(date -v-7d +%s 2>/dev/null || date -d '7 days ago' +%s)
            ;;
        month)
            created_gte=$(date -v-30d +%s 2>/dev/null || date -d '30 days ago' +%s)
            ;;
        *)
            echo "Error: Period must be 'day', 'week', or 'month'."
            exit 1
            ;;
    esac

    # Fetch charges in the date range
    local response
    response=$(stripe_api GET "/charges?limit=100&created[gte]=$created_gte")

    echo "$response" | python3 -c "
import sys, json

data = json.load(sys.stdin)
charges = data.get('data', [])

# Filter to Rick charges (by metadata on the charge or payment intent)
rick_charges = [c for c in charges if c.get('metadata', {}).get('agent') == 'rick']

total_revenue = sum(c.get('amount', 0) for c in rick_charges if c.get('status') == 'succeeded')
total_refunded = sum(c.get('amount_refunded', 0) for c in rick_charges)
net_revenue = total_revenue - total_refunded
count = len([c for c in rick_charges if c.get('status') == 'succeeded'])

period = '$period'

print(f'  Period:       Last {period}')
print(f'  Transactions: {count}')
print(f'  Gross:        \${total_revenue / 100:,.2f}')
print(f'  Refunded:     \${total_refunded / 100:,.2f}')
print(f'  Net Revenue:  \${net_revenue / 100:,.2f}')
print('')

if not rick_charges:
    print('  No Rick charges found in this period.')
    print('  (Charges must have metadata.agent=rick)')
" 2>/dev/null

    echo ""
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
    --recent)
        cmd_recent
        ;;
    --revenue)
        shift
        cmd_revenue "$@"
        ;;
    -h|--help)
        usage
        ;;
    *)
        echo "Unknown command: $1"
        usage
        ;;
esac
