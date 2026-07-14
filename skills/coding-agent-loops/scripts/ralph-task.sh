#!/usr/bin/env bash
# Launch a coding agent task in a persistent tmux session with Ralph retry loop.
# Completion sends an OpenClaw system event notification.
#
# Usage:
#   ralph-task.sh --name "fix-auth" --task "Fix the auth bug" [--repo ~/project]
#   ralph-task.sh --name "feature-x" --prd PRD.md [--repo ~/project]
#   ralph-task.sh --name "refactor" --task "Refactor DB" --agent claude [--repo ~/project]

set -euo pipefail

TASK_NAME=""
TASK_DESC=""
PRD_FILE=""
REPO_DIR="$(pwd)"
AGENT="codex"
MAX_ITERATIONS=""
PARALLEL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) TASK_NAME="$2"; shift 2 ;;
        --task) TASK_DESC="$2"; shift 2 ;;
        --prd) PRD_FILE="$2"; shift 2 ;;
        --repo) REPO_DIR="$2"; shift 2 ;;
        --agent) AGENT="$2"; shift 2 ;;
        --max-iterations) MAX_ITERATIONS="--max-iterations $2"; shift 2 ;;
        --parallel) PARALLEL="--parallel"; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$TASK_NAME" ]]; then
    echo "Error: --name required" >&2
    exit 1
fi

if [[ -z "$TASK_DESC" && -z "$PRD_FILE" ]]; then
    echo "Error: provide --task or --prd" >&2
    exit 1
fi

# Build ralphy command
RALPH_CMD="ralphy --${AGENT}"
if [[ -n "$PRD_FILE" ]]; then
    RALPH_CMD="${RALPH_CMD} --prd ${PRD_FILE}"
else
    RALPH_CMD="${RALPH_CMD} '${TASK_DESC}'"
fi
[[ -n "$MAX_ITERATIONS" ]] && RALPH_CMD="${RALPH_CMD} ${MAX_ITERATIONS}"
[[ -n "$PARALLEL" ]] && RALPH_CMD="${RALPH_CMD} ${PARALLEL}"

# Ensure tmux socket dir exists
mkdir -p ~/.tmux

# Launch in tmux with completion hook
echo "Launching ${TASK_NAME} in tmux (agent: ${AGENT}, repo: ${REPO_DIR})"
tmux -S ~/.tmux/sock new -d -s "${TASK_NAME}" \
    "export PATH=/opt/homebrew/bin:\$PATH; \
     cd ${REPO_DIR} && ${RALPH_CMD}; \
     EXIT_CODE=\$?; \
     echo \"EXITED: \$EXIT_CODE\"; \
     openclaw system event --text '${TASK_NAME} finished (exit \$EXIT_CODE) in ${REPO_DIR}' --mode now 2>/dev/null || true; \
     echo 'Completion hook sent. Session will stay alive for inspection.'; \
     sleep 999999"

echo "Session '${TASK_NAME}' started."
echo "  Check: tmux -S ~/.tmux/sock capture-pane -t ${TASK_NAME} -p | tail -20"
echo "  Kill:  tmux -S ~/.tmux/sock kill-session -t ${TASK_NAME}"
