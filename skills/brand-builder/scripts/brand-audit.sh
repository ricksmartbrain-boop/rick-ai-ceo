#!/usr/bin/env bash
# Audit brand presence across all channels.
#
# Usage:
#   brand-audit.sh --full                # Full audit (all channels)
#   brand-audit.sh --channel newsletter  # Newsletter only
#   brand-audit.sh --channel linkedin    # LinkedIn only
#   brand-audit.sh --channel twitter     # X/Twitter only
#   brand-audit.sh --channel instagram   # Instagram only

set -euo pipefail

CHANNEL=""
FULL=false

usage() {
    echo "Usage: brand-audit.sh [--full | --channel <name>]"
    echo ""
    echo "Options:"
    echo "  --full               Audit all channels"
    echo "  --channel <name>     Audit specific channel"
    echo ""
    echo "Channels: newsletter, linkedin, twitter, instagram"
    echo ""
    echo "Examples:"
    echo "  brand-audit.sh --full"
    echo "  brand-audit.sh --channel newsletter"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --full) FULL=true; shift ;;
        --channel) CHANNEL="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [ "$FULL" = false ] && [ -z "$CHANNEL" ]; then
    usage
fi

TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
DATE=$(date '+%Y-%m-%d')

echo "==========================================="
echo "  Brand Audit"
echo "  $TIMESTAMP"
echo "==========================================="
echo ""

# --- Newsletter (Beehiiv) ---

audit_newsletter() {
    echo "## Newsletter (Beehiiv)"
    echo "-------------------------------------------"
    echo ""

    BEEHIIV_API_KEY="${BEEHIIV_API_KEY:-}"
    BEEHIIV_PUBLICATION_ID="${BEEHIIV_PUBLICATION_ID:-}"

    if [ -n "$BEEHIIV_API_KEY" ] && [ -n "$BEEHIIV_PUBLICATION_ID" ]; then
        # Fetch subscriber stats
        STATS=$(curl -s -X GET \
            "https://api.beehiiv.com/v2/publications/$BEEHIIV_PUBLICATION_ID" \
            -H "Authorization: Bearer $BEEHIIV_API_KEY" \
            -H "Content-Type: application/json" 2>/dev/null) || true

        if echo "$STATS" | grep -q '"data"' 2>/dev/null; then
            echo "API connected. Raw stats:"
            echo "$STATS" | python3 -m json.tool 2>/dev/null || echo "$STATS"
        else
            echo "Could not fetch Beehiiv stats."
            echo "Check BEEHIIV_API_KEY and BEEHIIV_PUBLICATION_ID env vars."
        fi
    else
        echo "Beehiiv API not configured."
        echo "Set BEEHIIV_API_KEY and BEEHIIV_PUBLICATION_ID env vars."
    fi

    echo ""
    echo "Manual check:"
    echo "  - Subscriber count: [check Beehiiv dashboard]"
    echo "  - Avg open rate: [check Beehiiv dashboard]"
    echo "  - Avg click rate: [check Beehiiv dashboard]"
    echo "  - Publishing frequency: Weekly (Sunday)"
    echo "  - Total editions: 67+"
    echo ""
}

# --- LinkedIn ---

audit_linkedin() {
    echo "## LinkedIn"
    echo "-------------------------------------------"
    echo ""
    echo "Manual check (LinkedIn does not have a public API for follower metrics):"
    echo "  - Follower count: [check LinkedIn profile]"
    echo "  - Posts this week: [count recent posts]"
    echo "  - Avg engagement rate: [likes + comments / followers]"
    echo "  - Top-performing post this month: [check analytics]"
    echo ""
    echo "Target: 10,000 followers by Q2 2026"
    echo "Growth strategy: 3-5 posts/week, daily engagement, guest content"
    echo ""
}

# --- X/Twitter ---

audit_twitter() {
    echo "## X/Twitter"
    echo "-------------------------------------------"
    echo ""

    # Check if xpost CLI or X API is available
    if command -v xpost &> /dev/null; then
        echo "xpost CLI detected. Checking profile..."
        xpost profile 2>/dev/null || echo "Could not fetch X profile."
    else
        echo "Manual check (or configure xpost CLI):"
        echo "  - Follower count: [check X profile]"
        echo "  - Posts this week: [count recent tweets]"
        echo "  - Avg impressions: [check X analytics]"
        echo "  - Avg engagement rate: [check X analytics]"
    fi

    echo ""
    echo "Target: 5,000 followers by Q2 2026"
    echo "Growth strategy: 1-2 tweets/day, threads 2x/week, engage daily"
    echo ""
}

# --- Instagram ---

audit_instagram() {
    echo "## Instagram"
    echo "-------------------------------------------"
    echo ""

    SOCIAVAULT_API_KEY="${SOCIAVAULT_API_KEY:-}"

    if [ -n "$SOCIAVAULT_API_KEY" ]; then
        # Use SociaVault API for Instagram metrics
        echo "Fetching Instagram metrics via SociaVault..."

        # Note: Replace with Rick's brand Instagram handle
        HANDLE="${INSTAGRAM_HANDLE:-}"

        if [ -n "$HANDLE" ]; then
            RESULT=$(curl -s -X GET \
                "https://api.sociavault.com/v1/scrape/instagram/profile?handle=$HANDLE" \
                -H "X-API-Key: $SOCIAVAULT_API_KEY" 2>/dev/null) || true

            if echo "$RESULT" | grep -q '"followers"' 2>/dev/null; then
                echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"
            else
                echo "Could not fetch Instagram data for @$HANDLE"
            fi
        else
            echo "INSTAGRAM_HANDLE env var not set."
        fi
    else
        echo "SociaVault API not configured."
        echo "Set SOCIAVAULT_API_KEY env var."
    fi

    echo ""
    echo "Manual check:"
    echo "  - Follower count: [check Instagram]"
    echo "  - Posts this week: [count]"
    echo "  - Avg engagement rate: [likes + comments / followers]"
    echo ""
}

# --- Run audits ---

if [ "$FULL" = true ]; then
    audit_newsletter
    audit_linkedin
    audit_twitter
    audit_instagram

    echo "==========================================="
    echo ""
    echo "## Summary"
    echo ""
    echo "| Channel | Followers | Target (Q2) | Gap |"
    echo "|---------|-----------|-------------|-----|"
    echo "| Newsletter | [check] | 5,000 | [calc] |"
    echo "| LinkedIn | [check] | 10,000 | [calc] |"
    echo "| X/Twitter | [check] | 5,000 | [calc] |"
    echo "| Instagram | [check] | [TBD] | [calc] |"
    echo ""
    echo "Fill in current numbers to calculate gaps."
else
    case $CHANNEL in
        newsletter) audit_newsletter ;;
        linkedin) audit_linkedin ;;
        twitter|x) audit_twitter ;;
        instagram) audit_instagram ;;
        *) echo "Unknown channel: $CHANNEL"; echo "Valid: newsletter, linkedin, twitter, instagram"; exit 1 ;;
    esac
fi

echo "==========================================="
