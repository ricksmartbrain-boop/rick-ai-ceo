#!/usr/bin/env bash
# Cross-product funnel metrics.
#
# Usage:
#   funnel-status.sh                    # Full funnel report
#   funnel-status.sh --product brand    # Personal Brand only
#   funnel-status.sh --product pc       # Partner Connector only

set -euo pipefail

PRODUCT="all"

while [[ $# -gt 0 ]]; do
    case $1 in
        --product) PRODUCT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "═══════════════════════════════════════"
echo "  Growth Engine — Funnel Status"
echo "  $(date '+%Y-%m-%d %H:%M')"
echo "═══════════════════════════════════════"
echo ""

if [ "$PRODUCT" = "all" ] || [ "$PRODUCT" = "brand" ]; then
    echo "📣 Personal Brand Funnel"
    echo "---"
    echo "  Awareness:  LinkedIn posts, podcast episodes, guest appearances"
    echo "  Interest:   Newsletter subscriptions (67 editions published)"
    echo "  Trial:      Free content, lead magnets"
    echo "  Paid:       Paid newsletter tier (not yet launched)"
    echo "  Retained:   Open rates, engagement"
    echo "  Advocate:   Referrals, testimonials"
    echo ""
    echo "  📊 Key conversion: Newsletter subscribers → Paid tier"
    echo "  🎯 Target: 5,000 subscribers, 5% paid conversion = 250 × \$29 = \$7,250/mo"
    echo ""
fi

if [ "$PRODUCT" = "all" ] || [ "$PRODUCT" = "pc" ]; then
    echo "🤝 Partner Connector Funnel"
    echo "---"
    echo "  Awareness:  LinkedIn, referrals, Belkins network"
    echo "  Interest:   Demo request / signup"
    echo "  Trial:      First lead browse, reservation"
    echo "  Paid:       First lead purchase"
    echo "  Retained:   Monthly purchases, Stripe subscription"
    echo "  Advocate:   Partner referrals"
    echo ""
    echo "  📊 Key conversion: Demo → Active buyer"
    echo "  🎯 Target: 25 active partners × \$1,200 avg monthly = \$30,000/mo"
    echo ""
fi

if [ "$PRODUCT" = "all" ] || [ "$PRODUCT" = "agency" ]; then
    echo "👩 404 Agency Funnel"
    echo "---"
    echo "  Awareness:  Instagram content, reels, hashtags"
    echo "  Interest:   Follow, engage, DMs"
    echo "  Trial:      Free content, teasers"
    echo "  Paid:       Fanvue subscription"
    echo "  Retained:   Exclusive content, interaction"
    echo "  Advocate:   Share with friends"
    echo ""
    echo "  📊 Key conversion: IG followers → Fanvue subscribers"
    echo "  🎯 Target: 5,000 followers, 2% conversion = 100 × \$15 = \$1,500/mo (+ brand deals)"
    echo ""
fi

if [ "$PRODUCT" = "all" ] || [ "$PRODUCT" = "info" ]; then
    echo "📚 Info Products Funnel"
    echo "---"
    echo "  Awareness:  Newsletter, LinkedIn, podcast"
    echo "  Interest:   Free chapter download, waitlist signup"
    echo "  Trial:      Free mini-course or sample"
    echo "  Paid:       Course purchase (\$29-\$299)"
    echo "  Retained:   Upsell to premium, community"
    echo "  Advocate:   Student testimonials, affiliate program"
    echo ""
    echo "  📊 Key conversion: Email list → Course buyer"
    echo "  🎯 Target: 5,000 list × 3% conversion × \$99 avg = \$14,850/launch + evergreen"
    echo ""
fi

echo "═══════════════════════════════════════"
echo "  Cross-Product Synergies:"
echo "  Newsletter → Info Product buyers (direct)"
echo "  LinkedIn → Partner Connector demos (indirect)"
echo "  404 experiments → Brand strategy (learning)"
echo "═══════════════════════════════════════"
