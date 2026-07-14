#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${RICK_ENV_FILE:-$ROOT_DIR/config/rick.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/queue-info-product.sh \"Idea Title\" [price_usd] [product_type]" >&2
  exit 1
fi

IDEA="$1"
PRICE_USD="${2:-29}"
PRODUCT_TYPE="${3:-guide}"

bash "$ROOT_DIR/scripts/bootstrap.sh" >/dev/null
python3 "$ROOT_DIR/runtime/runner.py" queue-info-product --idea "$IDEA" --price-usd "$PRICE_USD" --product-type "$PRODUCT_TYPE"

