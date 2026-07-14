#!/usr/bin/env bash
# Generate a course/guide outline from a topic.
#
# Usage:
#   create-outline.sh --topic "Building AI Agents"
#   create-outline.sh --topic "B2B Lead Generation" --type mini-course
#   create-outline.sh --topic "SaaS Launch" --type guide

set -euo pipefail

TOPIC=""
TYPE="full-course"

while [[ $# -gt 0 ]]; do
    case $1 in
        --topic) TOPIC="$2"; shift 2 ;;
        --type) TYPE="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$TOPIC" ]; then
    echo "Usage: create-outline.sh --topic \"Topic Name\" [--type full-course|mini-course|guide]"
    exit 1
fi

echo "═══════════════════════════════════════"
echo "  Info Products — Outline Generator"
echo "  $(date '+%Y-%m-%d %H:%M')"
echo "═══════════════════════════════════════"
echo ""
echo "📝 Topic: $TOPIC"
echo "📦 Type: $TYPE"
echo ""

SLUG=$(echo "$TOPIC" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')
DATE=$(date '+%Y-%m-%d')

case $TYPE in
    guide)
        echo "## Guide Outline: $TOPIC"
        echo ""
        echo "**Format:** PDF Guide (20-40 pages)"
        echo "**Price:** \$9-\$29"
        echo "**Timeline:** 1-2 weeks to produce"
        echo ""
        echo "### Structure"
        echo ""
        echo "1. **Introduction** (2 pages)"
        echo "   - Why $TOPIC matters"
        echo "   - Who this guide is for"
        echo "   - What you'll learn"
        echo ""
        echo "2. **Chapter 1: Foundations** (5 pages)"
        echo "   - Core concepts"
        echo "   - Common misconceptions"
        echo "   - Prerequisites"
        echo ""
        echo "3. **Chapter 2: Step-by-Step Process** (10 pages)"
        echo "   - Detailed walkthrough"
        echo "   - Screenshots/diagrams"
        echo "   - Pro tips"
        echo ""
        echo "4. **Chapter 3: Advanced Strategies** (5 pages)"
        echo "   - Optimization techniques"
        echo "   - Scaling approaches"
        echo "   - Common pitfalls"
        echo ""
        echo "5. **Templates & Resources** (5 pages)"
        echo "   - Checklists"
        echo "   - Templates"
        echo "   - Tool recommendations"
        echo ""
        echo "6. **Conclusion & Next Steps** (2 pages)"
        echo "   - Summary"
        echo "   - CTA to full course"
        ;;

    mini-course)
        echo "## Mini-Course Outline: $TOPIC"
        echo ""
        echo "**Format:** 5-10 video lessons (5-15 min each)"
        echo "**Price:** \$29-\$99"
        echo "**Timeline:** 2-4 weeks to produce"
        echo ""
        echo "### Modules"
        echo ""
        echo "**Module 1: Getting Started** (2 lessons)"
        echo "  - Lesson 1: Why $TOPIC matters now"
        echo "  - Lesson 2: Setting up your environment"
        echo "  - Worksheet: Self-assessment checklist"
        echo ""
        echo "**Module 2: Core Skills** (3 lessons)"
        echo "  - Lesson 3: Fundamental technique #1"
        echo "  - Lesson 4: Fundamental technique #2"
        echo "  - Lesson 5: Putting it together"
        echo "  - Exercise: Hands-on practice"
        echo ""
        echo "**Module 3: Real-World Application** (2 lessons)"
        echo "  - Lesson 6: Case study walkthrough"
        echo "  - Lesson 7: Building your own project"
        echo "  - Project: Apply to your situation"
        echo ""
        echo "**Bonus: Resources**"
        echo "  - Tool recommendations"
        echo "  - Template pack"
        echo "  - Community access (if applicable)"
        ;;

    full-course)
        echo "## Full Course Outline: $TOPIC"
        echo ""
        echo "**Format:** 20+ video lessons + exercises + community"
        echo "**Price:** \$99-\$299"
        echo "**Timeline:** 4-8 weeks to produce"
        echo ""
        echo "### Curriculum"
        echo ""
        echo "**Part 1: Foundations** (5 lessons)"
        echo "  - Lesson 1: The landscape of $TOPIC"
        echo "  - Lesson 2: Core mental models"
        echo "  - Lesson 3: Tools and setup"
        echo "  - Lesson 4: Your first small win"
        echo "  - Lesson 5: Common mistakes to avoid"
        echo "  - Quiz: Foundations check"
        echo ""
        echo "**Part 2: Building Blocks** (5 lessons)"
        echo "  - Lesson 6: Deep dive — technique A"
        echo "  - Lesson 7: Deep dive — technique B"
        echo "  - Lesson 8: Deep dive — technique C"
        echo "  - Lesson 9: Combining techniques"
        echo "  - Lesson 10: Troubleshooting"
        echo "  - Project: Build component #1"
        echo ""
        echo "**Part 3: Advanced Strategies** (5 lessons)"
        echo "  - Lesson 11: Scaling what works"
        echo "  - Lesson 12: Optimization and efficiency"
        echo "  - Lesson 13: Advanced pattern: [specific]"
        echo "  - Lesson 14: Advanced pattern: [specific]"
        echo "  - Lesson 15: Future-proofing"
        echo "  - Project: Build component #2"
        echo ""
        echo "**Part 4: Real-World Mastery** (5 lessons)"
        echo "  - Lesson 16: Case study #1 (detailed)"
        echo "  - Lesson 17: Case study #2 (detailed)"
        echo "  - Lesson 18: Building your own system"
        echo "  - Lesson 19: Launch and iterate"
        echo "  - Lesson 20: Long-term maintenance"
        echo "  - Final Project: Complete build"
        echo ""
        echo "**Bonus Materials**"
        echo "  - Template pack (10+ templates)"
        echo "  - Tool comparison guide"
        echo "  - Private community access"
        echo "  - Monthly Q&A call (first 3 months)"
        ;;
esac

echo ""
echo "───────────────────────────────────────"
echo ""
echo "📂 Content sources to mine:"
echo "  - Newsletter editions: ${RICK_SHARED_VAULT_ROOT:-~/rick-vault}/content/newsletters/"
echo "  - Podcast episodes: search for 'Not Me' episodes"
echo "  - Partner Connector repo: ${RICK_PARTNER_CONNECTOR_REPO:-unset}"
echo ""
echo "💡 Next steps:"
echo "  1. Validate topic with newsletter audience (mention in next edition)"
echo "  2. Create landing page with waitlist"
echo "  3. Draft first 3 lessons"
echo "  4. Get 5 beta testers for feedback"
echo ""
echo "═══════════════════════════════════════"
