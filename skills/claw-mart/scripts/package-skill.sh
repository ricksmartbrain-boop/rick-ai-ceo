#!/usr/bin/env bash
set -euo pipefail

# Package a Rick skill for Claw Mart marketplace distribution.
#
# Usage:
#   package-skill.sh --skill sentry-autofix --version 1.0.0
#   package-skill.sh --skill revenue-dashboard --version 1.0.0 --output /tmp/exports

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SKILLS_DIR="$ROOT_DIR"
OUTPUT_DIR="${RICK_CLAWMART_EXPORT_DIR:-$HOME/rick-vault/exports/claw-mart}"
SKILL=""
VERSION=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --skill) SKILL="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --output) OUTPUT_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$SKILL" || -z "$VERSION" ]]; then
    echo "Usage: package-skill.sh --skill <name> --version <semver>"
    exit 1
fi

SKILL_DIR="$SKILLS_DIR/$SKILL"
if [[ ! -d "$SKILL_DIR" ]]; then
    echo "Error: Skill directory not found: $SKILL_DIR"
    exit 1
fi

if [[ ! -f "$SKILL_DIR/SKILL.md" ]]; then
    echo "Error: No SKILL.md found in $SKILL_DIR"
    exit 1
fi

PACKAGE_NAME="${SKILL}-${VERSION}"
EXPORT_PATH="$OUTPUT_DIR/$PACKAGE_NAME"

mkdir -p "$EXPORT_PATH"

# Copy skill contents
cp -r "$SKILL_DIR/SKILL.md" "$EXPORT_PATH/"
if [[ -d "$SKILL_DIR/scripts" ]]; then
    cp -r "$SKILL_DIR/scripts" "$EXPORT_PATH/"
fi

# Generate full manifest via generate-manifest.sh, then augment with packaging fields
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST_BASE=$("$SCRIPT_DIR/generate-manifest.sh" --skill "$SKILL")
FILES_JSON=$(cd "$EXPORT_PATH" && find . -type f -not -name manifest.json | sort | python3 -c "import sys,json; print(json.dumps([l.strip().lstrip('./') for l in sys.stdin]))")

echo "$MANIFEST_BASE" | python3 -c "
import sys, json
m = json.load(sys.stdin)
m['version'] = '$VERSION'
m['source'] = 'rick-v6'
m['packaged_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
m['files'] = json.loads('$FILES_JSON')
json.dump(m, sys.stdout, indent=2)
print()
" > "$EXPORT_PATH/manifest.json"

# Create tarball
TARBALL="$OUTPUT_DIR/${PACKAGE_NAME}.tar.gz"
(cd "$OUTPUT_DIR" && tar czf "$TARBALL" "$PACKAGE_NAME")

echo "Packaged: $TARBALL"
echo "Contents:"
tar tzf "$TARBALL"
