#!/bin/bash
# GA4 Setup Helper for meetrick.ai
# Run this once to configure GA4 access

set -e

SA_PATH="$HOME/.config/google/ga4-service-account.json"
RICK_ENV="$HOME/clawd/config/rick.env"

echo "═══════════════════════════════════════════════"
echo "  meetrick.ai — GA4 Setup"
echo "═══════════════════════════════════════════════"
echo ""

# Check if already configured
if [ -f "$SA_PATH" ]; then
    echo "✅ Service account JSON found at: $SA_PATH"
    SA_EMAIL=$(python3 -c "import json; d=json.load(open('$SA_PATH')); print(d.get('client_email','?'))" 2>/dev/null)
    echo "   Email: $SA_EMAIL"
else
    echo "⚠️  Service account JSON not found."
    echo ""
    echo "📋 Steps to create it:"
    echo ""
    echo "  1. Go to Google Cloud Console:"
    echo "     https://console.cloud.google.com/apis/library/analyticsdata.googleapis.com"
    echo "     → Enable 'Google Analytics Data API'"
    echo ""
    echo "  2. Create a service account:"
    echo "     https://console.cloud.google.com/iam-admin/serviceaccounts"
    echo "     → Create service account → name it 'rick-ga4'"
    echo "     → Grant no roles (we use GA4 property-level access)"
    echo "     → Create JSON key → Download it"
    echo ""
    echo "  3. Save the key:"
    echo "     mkdir -p ~/.config/google"
    echo "     mv ~/Downloads/your-key-file.json $SA_PATH"
    echo "     chmod 600 $SA_PATH"
    echo ""
fi

# Check GA4 Property ID
if grep -q "GA4_PROPERTY_ID=properties/" "$RICK_ENV" 2>/dev/null; then
    PROP=$(grep "GA4_PROPERTY_ID" "$RICK_ENV" | cut -d= -f2)
    echo "✅ GA4_PROPERTY_ID set in rick.env: $PROP"
else
    echo ""
    echo "⚠️  GA4_PROPERTY_ID not set in rick.env"
    echo ""
    echo "  4. Get your GA4 Property ID:"
    echo "     → Go to analytics.google.com"
    echo "     → Admin → Property Settings → Property ID (numeric, e.g. 123456789)"
    echo "     → Add to $RICK_ENV:"
    echo "        GA4_PROPERTY_ID=properties/YOUR_NUMERIC_ID"
    echo ""
fi

# Check if service account has GA4 access
if [ -f "$SA_PATH" ]; then
    SA_EMAIL=$(python3 -c "import json; d=json.load(open('$SA_PATH')); print(d.get('client_email','?'))" 2>/dev/null)
    echo ""
    echo "  5. Grant GA4 access to the service account:"
    echo "     → analytics.google.com → Admin → Property Access Management"
    echo "     → Add user: $SA_EMAIL"
    echo "     → Role: Viewer"
    echo ""
fi

echo ""
echo "─── Test your setup: ────────────────────────"
echo "  python3 ~/clawd/scripts/ga4-report.py"
echo "  python3 ~/clawd/scripts/ga4-report.py --report realtime"
echo "  python3 ~/clawd/scripts/ga4-report.py --days 30"
echo "─────────────────────────────────────────────"
