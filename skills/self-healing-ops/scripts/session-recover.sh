#!/usr/bin/env bash
set -euo pipefail

NAME=""
CMD=""
CHECK_ONLY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --cmd) CMD="$2"; shift 2 ;;
    --check-only) CHECK_ONLY=true; shift ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$NAME" ]]; then
  echo "--name is required" >&2
  exit 1
fi

SOCKET_PATH="${RICK_TMUX_SOCKET_PATH:-$HOME/.tmux/sock}"

if tmux -S "$SOCKET_PATH" has-session -t "$NAME" 2>/dev/null; then
  echo "alive:$NAME"
  exit 0
fi

if [[ "$CHECK_ONLY" == "true" ]]; then
  echo "missing:$NAME"
  exit 1
fi

if [[ -z "$CMD" ]]; then
  echo "session missing and no restart command provided" >&2
  exit 1
fi

mkdir -p "$(dirname "$SOCKET_PATH")"
tmux -S "$SOCKET_PATH" new -d -s "$NAME" "$CMD"
echo "restarted:$NAME"
