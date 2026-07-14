#!/usr/bin/env bash
# Partner Connector customer health dashboard.
#
# Displays partner activity, lead flow, and revenue indicators.
# Uses the Partner Connector API for data when available.

set -euo pipefail

echo "═══════════════════════════════════════"
echo "  Partner Connector — Customer Health"
echo "  $(date '+%Y-%m-%d %H:%M')"
echo "═══════════════════════════════════════"
echo ""

# API base URL
API_BASE="https://api.partners.belkins.io"

echo "📊 Revenue Target"
echo "---"
echo "  Target MRR: \$30,000"
echo "  Revenue model: Lead marketplace (Belkins → Partners)"
echo "  Future: Partner-to-partner trading (20% commission)"
echo ""

echo "🤝 Partner Overview"
echo "---"
echo "  Current setup: Belkins = sole seller, 10+ buyer partners"
echo ""
echo "  To get detailed partner data, use the API:"
echo "  curl -sf $API_BASE/v1/partners (requires auth token)"
echo ""

echo "📈 Key Metrics to Monitor"
echo "---"
echo "  1. Active partners (bought a lead in last 30 days)"
echo "  2. Leads listed this month"
echo "  3. Leads sold this month"
echo "  4. Average lead price"
echo "  5. Partner retention (monthly)"
echo "  6. Time from lead listing to sale"
echo ""

echo "🔍 Health Indicators"
echo "---"
echo "  ✅ Healthy: Partner bought 2+ leads in 30 days"
echo "  ⚠️  At risk: No purchases in 14+ days"
echo "  ❌ Churning: No activity in 30+ days"
echo ""

echo "💡 Growth Opportunities"
echo "---"
echo "  1. Partners approaching monthly lead budget → upsell"
echo "  2. Partners with high purchase rate → premium tier"
echo "  3. Inactive partners → re-engagement campaign"
echo "  4. New partner referrals from active partners"
echo ""

echo "═══════════════════════════════════════"
echo "  Use Stripe metrics for revenue data:"
echo "  python3 skills/metrics/scripts/stripe-metrics.py --period month"
echo "═══════════════════════════════════════"
