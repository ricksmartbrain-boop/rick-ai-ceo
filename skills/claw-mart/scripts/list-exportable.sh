#!/usr/bin/env bash
set -euo pipefail

# List skills that are ready for Claw Mart export.
# A skill is exportable if it has both SKILL.md and a scripts/ directory.

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo "Exportable skills:"
echo ""
printf "%-30s %-10s %-8s\n" "SKILL" "SCRIPTS" "STATUS"
printf "%-30s %-10s %-8s\n" "-----" "-------" "------"

for skill_dir in "$ROOT_DIR"/*/; do
    skill_name="$(basename "$skill_dir")"
    if [[ ! -f "$skill_dir/SKILL.md" ]]; then
        continue
    fi

    script_count=0
    if [[ -d "$skill_dir/scripts" ]]; then
        script_count=$(find "$skill_dir/scripts" -type f | wc -l | tr -d ' ')
    fi

    if [[ "$script_count" -gt 0 ]]; then
        status="ready"
    else
        status="docs-only"
    fi

    printf "%-30s %-10s %-8s\n" "$skill_name" "$script_count" "$status"
done
