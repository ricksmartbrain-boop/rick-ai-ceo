#!/usr/bin/env bash
set -euo pipefail
source ~/clawd/config/rick.env
python3 ~/rick-vault/scripts/nurture-dispatch.py 2>&1
