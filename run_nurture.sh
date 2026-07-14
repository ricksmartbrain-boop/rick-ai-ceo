#!/bin/bash
set -euo pipefail
source ~/clawd/config/rick.env
python3 /Users/rickthebot/rick-vault/scripts/nurture-dispatch.py 2>&1
