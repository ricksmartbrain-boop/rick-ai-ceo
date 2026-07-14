#!/usr/bin/env bash
set -a
source "$(dirname "$0")/../config/rick.env"
set +a
exec python3 "$(dirname "$0")/campaign-engine.py" --run
