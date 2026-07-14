#!/usr/bin/env bash
# Search Rick's vault by type, content, project, tier, or date range.
#
# Usage:
#   query-vault.sh --type TYPE         # Find notes by frontmatter type
#   query-vault.sh --search QUERY      # Full-text search
#   query-vault.sh --after DATE        # Notes created after date
#   query-vault.sh --recent N          # Last N modified files
#   query-vault.sh --tier hot          # Filter by hot/warm/cold memory tier
#   query-vault.sh --project NAME      # Filter by project slug

set -euo pipefail

RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TYPE=""
SEARCH=""
AFTER=""
RECENT=""
TIER=""
PROJECT=""
LIMIT="20"
RANKED=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --type) TYPE="$2"; shift 2 ;;
        --search) SEARCH="$2"; shift 2 ;;
        --after) AFTER="$2"; shift 2 ;;
        --recent) RECENT="$2"; shift 2 ;;
        --tier) TIER="$2"; shift 2 ;;
        --project) PROJECT="$2"; shift 2 ;;
        --limit) LIMIT="$2"; shift 2 ;;
        --ranked) RANKED="1"; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# --ranked delegates to BM25-scored memory-search.py instead of substring match
if [[ -n "$RANKED" && -n "$SEARCH" ]]; then
    RANKED_CMD=(python3 "$SCRIPT_DIR/memory-search.py" search "$SEARCH" --limit "$LIMIT")
    if [[ -n "$TIER" ]]; then
        RANKED_CMD+=(--tier "$TIER")
    fi
    exec "${RANKED_CMD[@]}"
fi

COMMAND=(python3 "$SCRIPT_DIR/rebuild-memory-index.py" query --limit "$LIMIT")

if [[ -n "$TYPE" ]]; then
    COMMAND+=(--type "$TYPE")
fi
if [[ -n "$SEARCH" ]]; then
    COMMAND+=(--search "$SEARCH")
fi
if [[ -n "$AFTER" ]]; then
    COMMAND+=(--after "$AFTER")
fi
if [[ -n "$RECENT" ]]; then
    COMMAND+=(--recent "$RECENT")
fi
if [[ -n "$TIER" ]]; then
    COMMAND+=(--tier "$TIER")
fi
if [[ -n "$PROJECT" ]]; then
    COMMAND+=(--project "$PROJECT")
fi

if [[ -n "$TYPE" || -n "$SEARCH" || -n "$AFTER" || -n "$RECENT" || -n "$TIER" || -n "$PROJECT" ]]; then
    "${COMMAND[@]}"
else
    echo "Usage:"
    echo "  query-vault.sh --type TYPE       # Find by frontmatter type"
    echo "  query-vault.sh --search QUERY    # Full-text substring search"
    echo "  query-vault.sh --search QUERY --ranked  # BM25-ranked search (better for multi-word queries)"
    echo "  query-vault.sh --after DATE      # Notes after date (YYYY-MM-DD)"
    echo "  query-vault.sh --recent N        # Last N modified files"
    echo "  query-vault.sh --tier hot        # Filter by hot/warm/cold"
    echo "  query-vault.sh --project NAME    # Filter by project"
    echo "  query-vault.sh --limit N         # Limit result count"
fi
