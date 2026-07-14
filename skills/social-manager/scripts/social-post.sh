#!/usr/bin/env bash
set -euo pipefail

# social-post.sh — Post content to LinkedIn or Instagram.

usage() {
  cat <<EOF
Usage: social-post.sh [OPTIONS]

Post content to social media platforms.

Options:
  --platform <linkedin|instagram|tiktok>  Target platform (required)
  --text <content>                        Post text/caption (required for posting)
  --image <path>                          Attach image (optional)
  --schedule <datetime>                   Schedule for later (ISO 8601)
  --list <platform>                       List recent posts for a platform
  -h, --help                              Show this help

Environment variables:
  LINKEDIN_ACCESS_TOKEN     LinkedIn OAuth 2.0 token
  LINKEDIN_PERSON_URN       LinkedIn person URN (urn:li:person:xxx)
  INSTAGRAM_ACCESS_TOKEN    Instagram Graph API token
  INSTAGRAM_BUSINESS_ID     Instagram Business Account ID
  TIKTOK_ACCESS_TOKEN       TikTok Content Posting API token (future)

Examples:
  social-post.sh --platform linkedin --text "AI agents are the new SaaS..."
  social-post.sh --platform instagram --text "Behind the scenes" --image photo.jpg
  social-post.sh --platform linkedin --text "Coming soon" --schedule "2026-03-10T14:00:00Z"
  social-post.sh --list linkedin
EOF
  exit 0
}

check_linkedin_env() {
  if [[ -z "${LINKEDIN_ACCESS_TOKEN:-}" ]]; then
    echo "Error: LINKEDIN_ACCESS_TOKEN env var is required" >&2
    exit 1
  fi
  if [[ -z "${LINKEDIN_PERSON_URN:-}" ]]; then
    echo "Error: LINKEDIN_PERSON_URN env var is required" >&2
    exit 1
  fi
}

check_instagram_env() {
  if [[ -z "${INSTAGRAM_ACCESS_TOKEN:-}" ]]; then
    echo "Error: INSTAGRAM_ACCESS_TOKEN env var is required" >&2
    exit 1
  fi
  if [[ -z "${INSTAGRAM_BUSINESS_ID:-}" ]]; then
    echo "Error: INSTAGRAM_BUSINESS_ID env var is required" >&2
    exit 1
  fi
}

post_linkedin() {
  local text="$1"
  local image="${2:-}"
  local schedule="${3:-}"

  check_linkedin_env

  if [[ -n "$schedule" ]]; then
    echo "Error: LinkedIn scheduling is not implemented here. Queue it in the runtime or publish immediately." >&2
    exit 1
  fi

  local payload
  payload=$(jq -n \
    --arg author "$LINKEDIN_PERSON_URN" \
    --arg text "$text" \
    '{
      author: $author,
      lifecycleState: "PUBLISHED",
      specificContent: {
        "com.linkedin.ugc.ShareContent": {
          shareCommentary: { text: $text },
          shareMediaCategory: "NONE"
        }
      },
      visibility: {
        "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
      }
    }')

  local response
  response=$(curl -s -X POST "https://api.linkedin.com/v2/ugcPosts" \
    -H "Authorization: Bearer ${LINKEDIN_ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -H "X-Restli-Protocol-Version: 2.0.0" \
    -d "$payload")

  local post_id
  post_id=$(echo "$response" | jq -r '.id // empty')

  if [[ -n "$post_id" ]]; then
    echo "Posted to LinkedIn (ID: $post_id)"
  else
    echo "Error posting to LinkedIn:" >&2
    echo "$response" | jq . >&2
    exit 1
  fi
}

post_instagram() {
  local text="$1"
  local image="${2:-}"
  local schedule="${3:-}"

  check_instagram_env

  if [[ -n "$schedule" ]]; then
    echo "Error: Instagram scheduling is not implemented here. Publish immediately or use a dedicated scheduler." >&2
    exit 1
  fi

  if [[ -z "$image" ]]; then
    echo "Error: Instagram requires an image. Use --image <path>." >&2
    exit 1
  fi

  if [[ ! -f "$image" ]]; then
    echo "Error: Image file not found: $image" >&2
    exit 1
  fi

  # Step 1: Create media container
  local create_response
  create_response=$(curl -s -X POST \
    "https://graph.facebook.com/v18.0/${INSTAGRAM_BUSINESS_ID}/media" \
    -F "image=@${image}" \
    -F "caption=${text}" \
    -F "access_token=${INSTAGRAM_ACCESS_TOKEN}")

  local creation_id
  creation_id=$(echo "$create_response" | jq -r '.id // empty')

  if [[ -z "$creation_id" ]]; then
    echo "Error creating Instagram media container:" >&2
    echo "$create_response" | jq . >&2
    exit 1
  fi

  # Step 2: Publish
  local publish_response
  publish_response=$(curl -s -X POST \
    "https://graph.facebook.com/v18.0/${INSTAGRAM_BUSINESS_ID}/media_publish" \
    -d "creation_id=${creation_id}" \
    -d "access_token=${INSTAGRAM_ACCESS_TOKEN}")

  local media_id
  media_id=$(echo "$publish_response" | jq -r '.id // empty')

  if [[ -n "$media_id" ]]; then
    echo "Posted to Instagram (Media ID: $media_id)"
  else
    echo "Error publishing to Instagram:" >&2
    echo "$publish_response" | jq . >&2
    exit 1
  fi
}

list_linkedin() {
  check_linkedin_env

  local response
  response=$(curl -s -X GET \
    "https://api.linkedin.com/v2/ugcPosts?q=authors&authors=List(${LINKEDIN_PERSON_URN})&count=10" \
    -H "Authorization: Bearer ${LINKEDIN_ACCESS_TOKEN}" \
    -H "X-Restli-Protocol-Version: 2.0.0")

  echo "Recent LinkedIn Posts"
  echo "====================="
  echo ""
  echo "$response" | jq -r '
    .elements[]? |
    "[\(.lifecycleState)] \(.specificContent["com.linkedin.ugc.ShareContent"].shareCommentary.text[:80])...
     Created: \(.created.time / 1000 | strftime("%Y-%m-%d %H:%M"))
     ID: \(.id)
     ---"
  '
}

list_instagram() {
  check_instagram_env

  local response
  response=$(curl -s -X GET \
    "https://graph.facebook.com/v18.0/${INSTAGRAM_BUSINESS_ID}/media?fields=id,caption,timestamp,like_count,comments_count&limit=10&access_token=${INSTAGRAM_ACCESS_TOKEN}")

  echo "Recent Instagram Posts"
  echo "======================"
  echo ""
  echo "$response" | jq -r '
    .data[]? |
    "\(.caption[:80] // "No caption")...
     Posted: \(.timestamp)
     Likes: \(.like_count // 0)  Comments: \(.comments_count // 0)
     ID: \(.id)
     ---"
  '
}

# Parse arguments
PLATFORM=""
TEXT=""
IMAGE=""
SCHEDULE=""
ACTION=""
LIST_PLATFORM=""

if [[ $# -eq 0 ]]; then
  usage
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)
      PLATFORM="$2"
      shift 2
      ;;
    --text)
      TEXT="$2"
      ACTION="post"
      shift 2
      ;;
    --image)
      IMAGE="$2"
      shift 2
      ;;
    --schedule)
      SCHEDULE="$2"
      shift 2
      ;;
    --list)
      ACTION="list"
      LIST_PLATFORM="$2"
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

case "$ACTION" in
  post)
    if [[ -z "$PLATFORM" ]]; then
      echo "Error: --platform is required" >&2
      exit 1
    fi
    if [[ -z "$TEXT" ]]; then
      echo "Error: --text is required for posting" >&2
      exit 1
    fi
    case "$PLATFORM" in
      linkedin)
        post_linkedin "$TEXT" "$IMAGE" "$SCHEDULE"
        ;;
      instagram)
        post_instagram "$TEXT" "$IMAGE" "$SCHEDULE"
        ;;
      tiktok)
        echo "Error: TikTok posting is not yet implemented." >&2
        exit 1
        ;;
      *)
        echo "Error: Unknown platform '$PLATFORM'. Use linkedin, instagram, or tiktok." >&2
        exit 1
        ;;
    esac
    ;;
  list)
    case "$LIST_PLATFORM" in
      linkedin)
        list_linkedin
        ;;
      instagram)
        list_instagram
        ;;
      *)
        echo "Error: Unknown platform '$LIST_PLATFORM'. Use linkedin or instagram." >&2
        exit 1
        ;;
    esac
    ;;
  *)
    echo "Error: No action specified. Use --text to post or --list to view posts." >&2
    exit 1
    ;;
esac
