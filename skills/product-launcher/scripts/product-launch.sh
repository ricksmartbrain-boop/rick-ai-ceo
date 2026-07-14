#!/usr/bin/env bash
# Manage the full product launch lifecycle.
#
# Usage:
#   product-launch.sh --plan <product>        # Generate launch plan
#   product-launch.sh --build <product>       # Kick off build phase
#   product-launch.sh --status <product>      # Check build/launch status
#   product-launch.sh --launch <product>      # Execute launch sequence
#   product-launch.sh --post-launch <product> # Monitor first 48h

set -euo pipefail

RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ACTION=""
PRODUCT=""

usage() {
    echo "Usage: product-launch.sh <action> <product>"
    echo ""
    echo "Actions:"
    echo "  --plan <product>        Generate launch plan (PRD + timeline)"
    echo "  --build <product>       Kick off build phase"
    echo "  --status <product>      Check build/launch status"
    echo "  --launch <product>      Execute launch sequence"
    echo "  --post-launch <product> Monitor first 48h"
    echo ""
    echo "Examples:"
    echo "  product-launch.sh --plan ai-agent-playbook"
    echo "  product-launch.sh --status ai-agent-playbook"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --plan) ACTION="plan"; PRODUCT="$2"; shift 2 ;;
        --build) ACTION="build"; PRODUCT="$2"; shift 2 ;;
        --status) ACTION="status"; PRODUCT="$2"; shift 2 ;;
        --launch) ACTION="launch"; PRODUCT="$2"; shift 2 ;;
        --post-launch) ACTION="post-launch"; PRODUCT="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [ -z "$ACTION" ] || [ -z "$PRODUCT" ]; then
    usage
fi

SLUG=$(echo "$PRODUCT" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')
PROJECT_DIR="$RICK_DATA_ROOT/projects/$SLUG"
DATE=$(date '+%Y-%m-%d')
TIMESTAMP=$(date '+%Y-%m-%d %H:%M')

echo "==========================================="
echo "  Product Launcher -- $ACTION"
echo "  $TIMESTAMP"
echo "==========================================="
echo ""
echo "Product: $PRODUCT"
echo "Project dir: $PROJECT_DIR"
echo ""

case $ACTION in
    plan)
        mkdir -p "$PROJECT_DIR"

        PLAN_FILE="$PROJECT_DIR/launch-plan.md"

        cat > "$PLAN_FILE" <<PLAN
# Launch Plan: $PRODUCT

**Created:** $DATE
**Status:** Planning
**Owner:** Rick (AI CEO)

## Product Requirements

- **Name:** $PRODUCT
- **Type:** [guide | mini-course | full-course | template-pack | tool]
- **Price:** \$[TBD]
- **Target audience:** [Define]
- **Problem solved:** [Define]
- **Unique angle:** [Define]

## Validation Checklist

- [ ] Market demand researched (Google Trends, competitors)
- [ ] Newsletter engagement checked on related topics
- [ ] Social validation post published
- [ ] Go/no-go decision made
- [ ] founder approval (if investment > \$500)

## Timeline

### Phase 0: Validation (Days 1-2)
- [ ] Research competitors and pricing
- [ ] Check newsletter analytics for topic interest
- [ ] Post validation content on LinkedIn/X
- [ ] Decision: proceed or pivot

### Phase 1: Build (Days 3-9)
- [ ] Generate outline (info-products skill)
- [ ] Write/record content
- [ ] Design and format
- [ ] Create Stripe product
- [ ] Quality review

### Phase 2: Pre-Launch (Days 10-12)
- [ ] Build landing page (website-builder skill)
- [ ] Set up checkout flow
- [ ] Draft launch newsletter
- [ ] Prepare social posts
- [ ] Set up email welcome sequence

### Phase 3: Launch (Day 13)
- [ ] Deploy landing page
- [ ] Send launch newsletter
- [ ] Post on all social channels
- [ ] Monitor sales in real-time

### Phase 4: Post-Launch (Days 14-20)
- [ ] Track daily sales metrics
- [ ] Collect customer feedback
- [ ] Gather testimonials
- [ ] A/B test landing page
- [ ] Write case study

## Success Metrics

| Metric | Target | Actual |
|--------|--------|--------|
| First-week sales | [TBD] | |
| Conversion rate | [TBD]% | |
| Revenue (month 1) | \$[TBD] | |
| Customer satisfaction | 4.5+ / 5 | |
| Refund rate | < 5% | |

## Notes

---
PLAN

        echo "Launch plan created: $PLAN_FILE"
        echo ""
        echo "Next steps:"
        echo "  1. Fill in product details (type, price, audience)"
        echo "  2. Run validation checklist"
        echo "  3. Get founder go/no-go if investment > \$500"
        echo "  4. Run: product-launch.sh --build $SLUG"
        ;;

    build)
        if [ ! -d "$PROJECT_DIR" ]; then
            echo "No project directory found. Run --plan first."
            echo "  product-launch.sh --plan $SLUG"
            exit 1
        fi

        echo "Build Phase"
        echo "-------------------------------------------"
        echo ""

        # Check if launch plan exists
        if [ -f "$PROJECT_DIR/launch-plan.md" ]; then
            echo "Launch plan: $PROJECT_DIR/launch-plan.md"
        else
            echo "Warning: No launch plan found. Consider running --plan first."
        fi

        echo ""
        echo "Build steps:"
        echo "  1. Generate outline:"
            echo "     bash \"$SKILLS_ROOT/info-products/scripts/create-outline.sh\" --topic \"$PRODUCT\""
        echo ""
        echo "  2. Create content directory:"
        mkdir -p "$PROJECT_DIR/content"
        mkdir -p "$PROJECT_DIR/assets"
        mkdir -p "$PROJECT_DIR/marketing"
        echo "     Created: $PROJECT_DIR/content/"
        echo "     Created: $PROJECT_DIR/assets/"
        echo "     Created: $PROJECT_DIR/marketing/"
        echo ""
        echo "  3. Start writing via Ralph coding loops:"
        echo "     Use tmux to run iterative draft sessions"
        echo ""
        echo "  4. Create Stripe product when content is ready:"
        echo "     bash scripts/create-product.sh --type guide --name \"$PRODUCT\" --price 29"
        echo ""

        # Update launch plan status
        if [ -f "$PROJECT_DIR/launch-plan.md" ]; then
            sed -i '' 's/\*\*Status:\*\* Planning/**Status:** Building/' "$PROJECT_DIR/launch-plan.md" 2>/dev/null || true
        fi
        ;;

    status)
        if [ ! -d "$PROJECT_DIR" ]; then
            echo "No project found for: $SLUG"
            echo ""
            echo "Available projects:"
            if [ -d "$RICK_DATA_ROOT/projects" ]; then
                ls -1 "$RICK_DATA_ROOT/projects/" 2>/dev/null || echo "  (none)"
            else
                echo "  (no projects directory)"
            fi
            exit 1
        fi

        echo "Project Status"
        echo "-------------------------------------------"
        echo ""

        # Check launch plan
        if [ -f "$PROJECT_DIR/launch-plan.md" ]; then
            STATUS=$(grep '^\*\*Status:\*\*' "$PROJECT_DIR/launch-plan.md" 2>/dev/null | head -1 || echo "Unknown")
            echo "Status: $STATUS"
        else
            echo "Status: No launch plan found"
        fi

        echo ""

        # List project files
        echo "Project files:"
        find "$PROJECT_DIR" -type f -name "*.md" 2>/dev/null | while read -r f; do
            echo "  $(basename "$f")"
        done

        echo ""

        # Check content directory
        if [ -d "$PROJECT_DIR/content" ]; then
            CONTENT_COUNT=$(find "$PROJECT_DIR/content" -type f 2>/dev/null | wc -l | tr -d ' ')
            echo "Content files: $CONTENT_COUNT"
        fi

        # Check marketing directory
        if [ -d "$PROJECT_DIR/marketing" ]; then
            MARKETING_COUNT=$(find "$PROJECT_DIR/marketing" -type f 2>/dev/null | wc -l | tr -d ' ')
            echo "Marketing files: $MARKETING_COUNT"
        fi

        echo ""

        # Check for Stripe product
        if [ -f "$PROJECT_DIR/stripe-product.json" ]; then
            echo "Stripe product: configured"
        else
            echo "Stripe product: not yet created"
        fi
        ;;

    launch)
        if [ ! -d "$PROJECT_DIR" ]; then
            echo "No project found for: $SLUG. Run --plan and --build first."
            exit 1
        fi

        echo "Launch Sequence"
        echo "-------------------------------------------"
        echo ""
        echo "Pre-launch checklist:"
        echo "  [ ] Landing page deployed and tested"
        echo "  [ ] Stripe checkout working (test purchase)"
        echo "  [ ] Launch newsletter drafted and reviewed"
        echo "  [ ] Social posts scheduled"
        echo "  [ ] Email welcome sequence active"
        echo ""
        echo "Launch actions:"
        echo ""
        echo "  1. Deploy landing page:"
            echo "     bash \"$SKILLS_ROOT/website-builder/scripts/deploy-site.sh\""
        echo ""
        echo "  2. Send launch newsletter:"
            echo "     bash \"$SKILLS_ROOT/newsletter/scripts/newsletter-send.sh\""
        echo ""
        echo "  3. Post on social channels:"
            echo "     bash \"$SKILLS_ROOT/social-manager/scripts/social-post.sh\""
        echo ""
        echo "  4. Monitor sales:"
            echo "     python3 \"$SKILLS_ROOT/revenue-dashboard/scripts/revenue-report.py\" --period day"
        echo ""

        # Update launch plan status
        if [ -f "$PROJECT_DIR/launch-plan.md" ]; then
            sed -i '' 's/\*\*Status:\*\* [A-Za-z]*/**Status:** Launched/' "$PROJECT_DIR/launch-plan.md" 2>/dev/null || true
        fi

        # Create launch log
        cat > "$PROJECT_DIR/launch-log.md" <<LOG
# Launch Log: $PRODUCT

**Launch date:** $DATE

## Day 1 ($DATE)
- Launch executed
- Newsletter sent
- Social posts published

## Metrics

| Date | Revenue | Units | Conversion | Notes |
|------|---------|-------|------------|-------|
| $DATE | | | | Launch day |

LOG

        echo "Launch log created: $PROJECT_DIR/launch-log.md"
        ;;

    post-launch)
        if [ ! -d "$PROJECT_DIR" ]; then
            echo "No project found for: $SLUG"
            exit 1
        fi

        echo "Post-Launch Monitor"
        echo "-------------------------------------------"
        echo ""

        # Show launch log if exists
        if [ -f "$PROJECT_DIR/launch-log.md" ]; then
            echo "Launch log:"
            cat "$PROJECT_DIR/launch-log.md"
        else
            echo "No launch log found. Was the product launched?"
            echo "  Run: product-launch.sh --launch $SLUG"
            exit 1
        fi

        echo ""
        echo "-------------------------------------------"
        echo ""
        echo "Post-launch tasks (first 48 hours):"
        echo ""
        echo "  1. Check sales metrics:"
            echo "     python3 \"$SKILLS_ROOT/revenue-dashboard/scripts/revenue-report.py\" --period day"
        echo ""
        echo "  2. Respond to customer feedback"
        echo "     Check email, social mentions, support requests"
        echo ""
        echo "  3. Gather testimonials:"
        echo "     Reach out to first buyers for quotes"
        echo ""
        echo "  4. A/B test landing page:"
        echo "     Test headline, CTA, and pricing variations"
        echo ""
        echo "  5. Post-launch content:"
        echo "     Share early results, social proof, behind-the-scenes"
        echo ""

        # Update launch plan status
        if [ -f "$PROJECT_DIR/launch-plan.md" ]; then
            sed -i '' 's/\*\*Status:\*\* [A-Za-z]*/**Status:** Post-Launch/' "$PROJECT_DIR/launch-plan.md" 2>/dev/null || true
        fi
        ;;
esac

echo ""
echo "==========================================="
