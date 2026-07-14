#!/usr/bin/env bash
set -euo pipefail

# email-sequence.sh — Manage email drip sequences.

RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
SEQUENCES_DIR="$RICK_DATA_ROOT/mailbox/sequences"

usage() {
  cat <<EOF
Usage: email-sequence.sh [OPTIONS]

Manage email drip sequences.

Options:
  --create <name>                                Create a new sequence
  --add-step <name> --delay <days> --template <file>  Add an email step to a sequence
  --trigger <name> --email <addr>                Enroll an email address in a sequence
  --status <name>                                Show sequence stats
  --list                                         List all sequences
  -h, --help                                     Show this help

Storage:
  Sequences are stored in \$RICK_DATA_ROOT/mailbox/sequences/<name>/

Examples:
  email-sequence.sh --create welcome
  email-sequence.sh --add-step welcome --delay 3 --template \$RICK_DATA_ROOT/mailbox/sequences/welcome/day3.md
  email-sequence.sh --trigger welcome --email subscriber@example.com
  email-sequence.sh --status welcome
  email-sequence.sh --list
EOF
  exit 0
}

cmd_create() {
  local name="$1"
  local seq_dir="${SEQUENCES_DIR}/${name}"

  if [[ -d "$seq_dir" ]]; then
    echo "Error: Sequence '$name' already exists at $seq_dir" >&2
    exit 1
  fi

  mkdir -p "$seq_dir"

  # Create sequence config
  cat > "${seq_dir}/sequence.json" <<JSON
{
  "name": "$name",
  "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "status": "active",
  "steps": [],
  "enrollments": []
}
JSON

  echo "Created sequence '$name' at $seq_dir"
  echo ""
  echo "Next steps:"
  echo "  1. Create template files in $seq_dir/"
  echo "  2. Add steps: email-sequence.sh --add-step $name --delay 0 --template ${seq_dir}/welcome.md"
}

cmd_add_step() {
  local name="$1"
  local delay="$2"
  local template="$3"
  local seq_dir="${SEQUENCES_DIR}/${name}"
  local config="${seq_dir}/sequence.json"

  if [[ ! -f "$config" ]]; then
    echo "Error: Sequence '$name' not found. Create it first with --create." >&2
    exit 1
  fi

  if [[ ! -f "$template" ]]; then
    echo "Error: Template file not found: $template" >&2
    exit 1
  fi

  # Validate delay is a number
  if ! [[ "$delay" =~ ^[0-9]+$ ]]; then
    echo "Error: --delay must be a non-negative integer (days)" >&2
    exit 1
  fi

  # Get current step count
  local step_count
  step_count=$(jq '.steps | length' "$config")

  local step_num=$((step_count + 1))

  # Copy template into sequence directory if not already there
  local template_basename
  template_basename=$(basename "$template")
  local local_template="${seq_dir}/${template_basename}"

  if [[ "$(realpath "$template")" != "$(realpath "$local_template" 2>/dev/null || echo '')" ]]; then
    cp "$template" "$local_template"
  fi

  # Add step to config
  local updated
  updated=$(jq \
    --arg delay "$delay" \
    --arg template "$template_basename" \
    --arg num "$step_num" \
    '.steps += [{
      step: ($num | tonumber),
      delay_days: ($delay | tonumber),
      template: $template,
      added: (now | strftime("%Y-%m-%dT%H:%M:%SZ"))
    }]' "$config")

  echo "$updated" > "$config"

  echo "Added step $step_num to '$name': send '${template_basename}' after $delay days"
}

cmd_trigger() {
  local name="$1"
  local email="$2"
  local seq_dir="${SEQUENCES_DIR}/${name}"
  local config="${seq_dir}/sequence.json"

  if [[ ! -f "$config" ]]; then
    echo "Error: Sequence '$name' not found." >&2
    exit 1
  fi

  # Check if already enrolled
  local already_enrolled
  already_enrolled=$(jq --arg email "$email" '.enrollments[] | select(.email == $email) | .email' "$config" 2>/dev/null || echo "")

  if [[ -n "$already_enrolled" ]]; then
    echo "Warning: $email is already enrolled in '$name'" >&2
    return
  fi

  # Add enrollment
  local updated
  updated=$(jq \
    --arg email "$email" \
    '.enrollments += [{
      email: $email,
      enrolled_at: (now | strftime("%Y-%m-%dT%H:%M:%SZ")),
      current_step: 0,
      status: "active"
    }]' "$config")

  echo "$updated" > "$config"

  local step_count
  step_count=$(jq '.steps | length' "$config")

  echo "Enrolled $email in '$name' ($step_count steps)"
}

cmd_status() {
  local name="$1"
  local seq_dir="${SEQUENCES_DIR}/${name}"
  local config="${seq_dir}/sequence.json"

  if [[ ! -f "$config" ]]; then
    echo "Error: Sequence '$name' not found." >&2
    exit 1
  fi

  echo "Sequence: $name"
  echo "================"

  local status
  status=$(jq -r '.status' "$config")
  echo "Status: $status"

  local created
  created=$(jq -r '.created' "$config")
  echo "Created: $created"
  echo ""

  echo "Steps:"
  jq -r '.steps[] | "  Step \(.step): Send \(.template) after \(.delay_days) days"' "$config" 2>/dev/null || echo "  (no steps)"
  echo ""

  local total_enrolled
  total_enrolled=$(jq '.enrollments | length' "$config")

  local active_enrolled
  active_enrolled=$(jq '[.enrollments[] | select(.status == "active")] | length' "$config")

  local completed_enrolled
  completed_enrolled=$(jq '[.enrollments[] | select(.status == "completed")] | length' "$config")

  echo "Enrollments:"
  echo "  Total: $total_enrolled"
  echo "  Active: $active_enrolled"
  echo "  Completed: $completed_enrolled"

  if [[ "$total_enrolled" -gt 0 ]]; then
    echo ""
    echo "Recent enrollments:"
    jq -r '.enrollments | sort_by(.enrolled_at) | reverse | .[:5][] |
      "  \(.email) | Step \(.current_step) | \(.status) | Enrolled: \(.enrolled_at)"
    ' "$config" 2>/dev/null || true
  fi
}

cmd_list() {
  if [[ ! -d "$SEQUENCES_DIR" ]]; then
    echo "No sequences found. Create one with --create." >&2
    return
  fi

  echo "Email Sequences"
  echo "==============="
  echo ""

  local found=false
  for config in "${SEQUENCES_DIR}"/*/sequence.json; do
    if [[ ! -f "$config" ]]; then
      continue
    fi
    found=true

    local name
    name=$(jq -r '.name' "$config")

    local status
    status=$(jq -r '.status' "$config")

    local steps
    steps=$(jq '.steps | length' "$config")

    local enrollments
    enrollments=$(jq '.enrollments | length' "$config")

    local active
    active=$(jq '[.enrollments[] | select(.status == "active")] | length' "$config")

    echo "[$status] $name"
    echo "  Steps: $steps | Enrolled: $enrollments ($active active)"
    echo "  ---"
  done

  if [[ "$found" == "false" ]]; then
    echo "No sequences found. Create one with --create."
  fi
}

# Parse arguments
ACTION=""
SEQ_NAME=""
DELAY=""
TEMPLATE=""
EMAIL=""

if [[ $# -eq 0 ]]; then
  usage
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --create)
      ACTION="create"
      SEQ_NAME="$2"
      shift 2
      ;;
    --add-step)
      ACTION="add-step"
      SEQ_NAME="$2"
      shift 2
      ;;
    --delay)
      DELAY="$2"
      shift 2
      ;;
    --template)
      TEMPLATE="$2"
      shift 2
      ;;
    --trigger)
      ACTION="trigger"
      SEQ_NAME="$2"
      shift 2
      ;;
    --email)
      EMAIL="$2"
      shift 2
      ;;
    --status)
      ACTION="status"
      SEQ_NAME="$2"
      shift 2
      ;;
    --list)
      ACTION="list"
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
  create)
    cmd_create "$SEQ_NAME"
    ;;
  add-step)
    if [[ -z "$DELAY" ]]; then
      echo "Error: --delay is required with --add-step" >&2
      exit 1
    fi
    if [[ -z "$TEMPLATE" ]]; then
      echo "Error: --template is required with --add-step" >&2
      exit 1
    fi
    cmd_add_step "$SEQ_NAME" "$DELAY" "$TEMPLATE"
    ;;
  trigger)
    if [[ -z "$EMAIL" ]]; then
      echo "Error: --email is required with --trigger" >&2
      exit 1
    fi
    cmd_trigger "$SEQ_NAME" "$EMAIL"
    ;;
  status)
    cmd_status "$SEQ_NAME"
    ;;
  list)
    cmd_list
    ;;
  *)
    echo "Error: No action specified." >&2
    usage
    ;;
esac
