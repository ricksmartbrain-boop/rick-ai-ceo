#!/usr/bin/env bash
set -euo pipefail

# social-metrics.sh — Track social media performance across platforms.

usage() {
  cat <<EOF
Usage: social-metrics.sh [OPTIONS]

Track social media performance.

Options:
  --platform <linkedin|instagram|all>   Target platform (default: all)
  --period <day|week|month>             Time period (default: week)
  -h, --help                            Show this help

Environment variables:
  LINKEDIN_ACCESS_TOKEN     LinkedIn OAuth 2.0 token
  LINKEDIN_PERSON_URN       LinkedIn person URN
  INSTAGRAM_ACCESS_TOKEN    Instagram Graph API token
  INSTAGRAM_BUSINESS_ID     Instagram Business Account ID

Examples:
  social-metrics.sh --platform linkedin --period week
  social-metrics.sh --platform all --period month
  social-metrics.sh --period day
EOF
  exit 0
}

period_to_seconds() {
  case "$1" in
    day)   echo 86400 ;;
    week)  echo 604800 ;;
    month) echo 2592000 ;;
  esac
}

metrics_linkedin() {
  local period="$1"

  if [[ -z "${LINKEDIN_ACCESS_TOKEN:-}" ]]; then
    echo "LinkedIn: LINKEDIN_ACCESS_TOKEN not set, skipping." >&2
    return
  fi

  echo "LinkedIn Metrics ($period)"
  echo "=========================="

  # Get recent posts
  local response
  response=$(curl -s -X GET \
    "https://api.linkedin.com/v2/ugcPosts?q=authors&authors=List(${LINKEDIN_PERSON_URN})&count=20" \
    -H "Authorization: Bearer ${LINKEDIN_ACCESS_TOKEN}" \
    -H "X-Restli-Protocol-Version: 2.0.0")

  local post_count
  post_count=$(echo "$response" | jq '[.elements[]?] | length')

  echo "Posts in period: $post_count"

  # Get network stats
  local network_response
  network_response=$(curl -s -X GET \
    "https://api.linkedin.com/v2/networkSizes/${LINKEDIN_PERSON_URN}?edgeType=CompanyFollowedByMember" \
    -H "Authorization: Bearer ${LINKEDIN_ACCESS_TOKEN}" \
    -H "X-Restli-Protocol-Version: 2.0.0" 2>/dev/null || echo '{}')

  local connections
  connections=$(echo "$network_response" | jq -r '.firstDegreeSize // "N/A"')

  echo "Connections: $connections"
  echo ""
}

metrics_instagram() {
  local period="$1"

  if [[ -z "${INSTAGRAM_ACCESS_TOKEN:-}" ]]; then
    echo "Instagram: INSTAGRAM_ACCESS_TOKEN not set, skipping." >&2
    return
  fi

  echo "Instagram Metrics ($period)"
  echo "============================"

  # Get account info
  local account_response
  account_response=$(curl -s -X GET \
    "https://graph.facebook.com/v18.0/${INSTAGRAM_BUSINESS_ID}?fields=followers_count,media_count&access_token=${INSTAGRAM_ACCESS_TOKEN}")

  local followers
  followers=$(echo "$account_response" | jq -r '.followers_count // "N/A"')
  local media_count
  media_count=$(echo "$account_response" | jq -r '.media_count // "N/A"')

  echo "Followers: $followers"
  echo "Total Posts: $media_count"

  # Get insights for the period
  local period_param
  case "$period" in
    day)   period_param="day" ;;
    week)  period_param="week" ;;
    month) period_param="days_28" ;;
  esac

  local insights_response
  insights_response=$(curl -s -X GET \
    "https://graph.facebook.com/v18.0/${INSTAGRAM_BUSINESS_ID}/insights?metric=impressions,reach,profile_views&period=${period_param}&access_token=${INSTAGRAM_ACCESS_TOKEN}" 2>/dev/null || echo '{}')

  echo "$insights_response" | jq -r '
    .data[]? |
    "\(.title // .name): \(.values[-1].value // "N/A")"
  ' 2>/dev/null || echo "(Insights unavailable for this period)"

  # Get recent posts with engagement
  local posts_response
  posts_response=$(curl -s -X GET \
    "https://graph.facebook.com/v18.0/${INSTAGRAM_BUSINESS_ID}/media?fields=id,caption,timestamp,like_count,comments_count&limit=10&access_token=${INSTAGRAM_ACCESS_TOKEN}")

  local total_likes
  total_likes=$(echo "$posts_response" | jq '[.data[]? | .like_count // 0] | add // 0')
  local total_comments
  total_comments=$(echo "$posts_response" | jq '[.data[]? | .comments_count // 0] | add // 0')
  local recent_count
  recent_count=$(echo "$posts_response" | jq '[.data[]?] | length')

  if [[ "$recent_count" -gt 0 && "$followers" != "N/A" && "$followers" -gt 0 ]]; then
    local engagement
    engagement=$(echo "scale=2; ($total_likes + $total_comments) / $recent_count / $followers * 100" | bc 2>/dev/null || echo "N/A")
    echo "Engagement Rate: ${engagement}%"
  fi

  echo ""

  # Top posts
  echo "Top Posts (by likes):"
  echo "$posts_response" | jq -r '
    [.data[]?] | sort_by(-.like_count) | .[:3][] |
    "  \(.like_count) likes | \(.comments_count) comments | \(.caption[:60] // "No caption")..."
  ' 2>/dev/null || echo "  (No posts found)"

  echo ""
}

# Parse arguments
PLATFORM="all"
PERIOD="week"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)
      PLATFORM="$2"
      shift 2
      ;;
    --period)
      PERIOD="$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      ;;
  esac
done

# Validate period
case "$PERIOD" in
  day|week|month) ;;
  *)
    echo "Error: --period must be one of: day, week, month" >&2
    exit 1
    ;;
esac

# Validate platform
case "$PLATFORM" in
  linkedin|instagram|all) ;;
  *)
    echo "Error: --platform must be one of: linkedin, instagram, all" >&2
    exit 1
    ;;
esac

echo "Social Media Metrics Report"
echo "==========================="
echo "Period: $PERIOD"
echo "Date: $(date +%Y-%m-%d)"
echo ""

case "$PLATFORM" in
  linkedin)
    metrics_linkedin "$PERIOD"
    ;;
  instagram)
    metrics_instagram "$PERIOD"
    ;;
  all)
    metrics_linkedin "$PERIOD"
    metrics_instagram "$PERIOD"
    ;;
esac
