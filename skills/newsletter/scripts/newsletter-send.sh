#!/usr/bin/env bash
set -euo pipefail

# newsletter-send.sh — Publish, schedule, and track newsletter editions via Beehiiv API.

BEEHIIV_BASE="https://api.beehiiv.com/v2"
BEEHIIV_PUBLICATION="${BEEHIIV_PUB_ID:-${BEEHIIV_PUBLICATION_ID:-}}"

usage() {
  cat <<EOF
Usage: newsletter-send.sh [OPTIONS]

Publish, schedule, and track newsletter editions.

Options:
  --platform <beehiiv|substack>   Target platform (default: beehiiv)
  --draft <file>                  Markdown file to publish
  --schedule <datetime>           Schedule for later (ISO 8601, e.g. 2026-03-10T09:00:00-05:00)
  --list                          List recent editions with open/click rates
  --stats                         Subscriber count, growth rate, avg open rate
  --subscribers                   Export subscriber count
  -h, --help                      Show this help

Environment variables:
  BEEHIIV_API_KEY   Beehiiv API key (required)
  BEEHIIV_PUB_ID    Beehiiv publication ID (required)

Examples:
  newsletter-send.sh --platform beehiiv --draft draft.md
  newsletter-send.sh --draft draft.md --schedule "2026-03-10T09:00:00-05:00"
  newsletter-send.sh --list
  newsletter-send.sh --stats
EOF
  exit 0
}

check_env() {
  if [[ -z "${BEEHIIV_API_KEY:-}" ]]; then
    echo "Error: BEEHIIV_API_KEY env var is required" >&2
    exit 1
  fi
  if [[ -z "${BEEHIIV_PUBLICATION:-}" ]]; then
    echo "Error: BEEHIIV_PUB_ID or BEEHIIV_PUBLICATION_ID env var is required" >&2
    exit 1
  fi
}

beehiiv_request() {
  local method="$1"
  local endpoint="$2"
  shift 2
  curl -s -X "$method" \
    "${BEEHIIV_BASE}/publications/${BEEHIIV_PUBLICATION}${endpoint}" \
    -H "Authorization: Bearer ${BEEHIIV_API_KEY}" \
    -H "Content-Type: application/json" \
    "$@"
}

cmd_send() {
  local draft_file="$1"
  local schedule="${2:-}"
  local platform="${3:-beehiiv}"

  if [[ "$platform" != "beehiiv" ]]; then
    echo "Error: Only beehiiv is supported for automated sending. For Substack, draft locally and post manually." >&2
    exit 1
  fi

  if [[ ! -f "$draft_file" ]]; then
    echo "Error: Draft file not found: $draft_file" >&2
    exit 1
  fi

  check_env

  local title
  title=$(head -1 "$draft_file" | sed 's/^#\s*//')
  local content
  content=$(tail -n +2 "$draft_file")

  local payload
  payload=$(jq -n \
    --arg title "$title" \
    --arg content "$content" \
    --arg status "$(if [[ -n "$schedule" ]]; then echo "scheduled"; else echo "draft"; fi)" \
    '{
      title: $title,
      content: [{ type: "html", html: $content }],
      status: $status
    }')

  if [[ -n "$schedule" ]]; then
    payload=$(echo "$payload" | jq --arg dt "$schedule" '. + { scheduled_at: $dt }')
  fi

  local response
  response=$(beehiiv_request POST "/posts" -d "$payload")

  local post_id
  post_id=$(echo "$response" | jq -r '.data.id // empty')

  if [[ -n "$post_id" ]]; then
    if [[ -n "$schedule" ]]; then
      echo "Scheduled edition '$title' for $schedule (ID: $post_id)"
    else
      echo "Created draft '$title' (ID: $post_id)"
    fi
  else
    echo "Error creating post:" >&2
    echo "$response" | jq . >&2
    exit 1
  fi
}

cmd_list() {
  check_env

  local response
  response=$(beehiiv_request GET "/posts?limit=10&order_by=publish_date&direction=desc")

  echo "Recent Newsletter Editions"
  echo "=========================="
  echo ""

  echo "$response" | jq -r '
    .data[] |
    "[\(.status)] \(.title)
     Published: \(.publish_date // "N/A")
     Opens: \(.stats.open_rate // "N/A")  Clicks: \(.stats.click_rate // "N/A")
     ID: \(.id)
     ---"
  '
}

cmd_stats() {
  check_env

  local subs_response
  subs_response=$(beehiiv_request GET "/subscriptions?limit=1")

  local posts_response
  posts_response=$(beehiiv_request GET "/posts?limit=20&status=confirmed&order_by=publish_date&direction=desc")

  local total_subs
  total_subs=$(echo "$subs_response" | jq -r '.total_results // 0')

  local avg_open_rate
  avg_open_rate=$(echo "$posts_response" | jq -r '
    [.data[] | .stats.open_rate // 0 | select(. > 0)] |
    if length > 0 then (add / length * 100 | floor / 100 | tostring) + "%" else "N/A" end
  ')

  local total_posts
  total_posts=$(echo "$posts_response" | jq -r '.total_results // 0')

  echo "Newsletter Stats"
  echo "================"
  echo "Total Subscribers: $total_subs"
  echo "Total Posts:       $total_posts"
  echo "Avg Open Rate:     $avg_open_rate"
}

cmd_subscribers() {
  check_env

  local response
  response=$(beehiiv_request GET "/subscriptions?limit=1")

  local total
  total=$(echo "$response" | jq -r '.total_results // 0')

  echo "$total"
}

# Parse arguments
PLATFORM="beehiiv"
DRAFT=""
SCHEDULE=""
ACTION=""

if [[ $# -eq 0 ]]; then
  usage
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)
      PLATFORM="$2"
      shift 2
      ;;
    --draft)
      DRAFT="$2"
      ACTION="send"
      shift 2
      ;;
    --schedule)
      SCHEDULE="$2"
      shift 2
      ;;
    --list)
      ACTION="list"
      shift
      ;;
    --stats)
      ACTION="stats"
      shift
      ;;
    --subscribers)
      ACTION="subscribers"
      shift
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

case "$ACTION" in
  send)
    cmd_send "$DRAFT" "$SCHEDULE" "$PLATFORM"
    ;;
  list)
    cmd_list
    ;;
  stats)
    cmd_stats
    ;;
  subscribers)
    cmd_subscribers
    ;;
  *)
    echo "Error: No action specified. Use --draft, --list, --stats, or --subscribers." >&2
    exit 1
    ;;
esac
