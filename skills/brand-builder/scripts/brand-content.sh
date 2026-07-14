#!/usr/bin/env bash
# Generate content drafts for any platform.
#
# Usage:
#   brand-content.sh --type thread --pillar ai --hook "Built an AI CEO in 48 hours"
#   brand-content.sh --type article --pillar entrepreneurship --hook "First $10K month"
#   brand-content.sh --type post --pillar building --hook "My agent stack"
#   brand-content.sh --type story --pillar lessons --hook "The launch that flopped"

set -euo pipefail

TYPE=""
PILLAR=""
HOOK=""

usage() {
    echo "Usage: brand-content.sh --type <type> --pillar <pillar> --hook <idea>"
    echo ""
    echo "Options:"
    echo "  --type    Content type: thread | article | post | story"
    echo "  --pillar  Content pillar: ai | entrepreneurship | building | lessons"
    echo "  --hook    Content hook or angle (the main idea)"
    echo ""
    echo "Examples:"
    echo "  brand-content.sh --type thread --pillar ai --hook 'Built an AI CEO in 48 hours'"
    echo "  brand-content.sh --type article --pillar entrepreneurship --hook 'First 10K month'"
    echo "  brand-content.sh --type post --pillar building --hook 'My agent stack'"
    echo "  brand-content.sh --type story --pillar lessons --hook 'The launch that flopped'"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --type) TYPE="$2"; shift 2 ;;
        --pillar) PILLAR="$2"; shift 2 ;;
        --hook) HOOK="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [ -z "$TYPE" ] || [ -z "$PILLAR" ] || [ -z "$HOOK" ]; then
    echo "Error: --type, --pillar, and --hook are all required."
    echo ""
    usage
fi

# Validate type
case $TYPE in
    thread|article|post|story) ;;
    *) echo "Error: Invalid type '$TYPE'. Must be: thread, article, post, or story."; exit 1 ;;
esac

# Validate pillar
case $PILLAR in
    ai) PILLAR_LABEL="AI & Automation" ;;
    entrepreneurship) PILLAR_LABEL="Entrepreneurship" ;;
    building) PILLAR_LABEL="Building in Public" ;;
    lessons) PILLAR_LABEL="Deep Dives & Lessons" ;;
    *) echo "Error: Invalid pillar '$PILLAR'. Must be: ai, entrepreneurship, building, or lessons."; exit 1 ;;
esac

DATE=$(date '+%Y-%m-%d')

case $TYPE in
    thread)
        echo "# X/Twitter Thread Draft"
        echo ""
        echo "**Pillar:** $PILLAR_LABEL"
        echo "**Hook:** $HOOK"
        echo "**Date:** $DATE"
        echo ""
        echo "---"
        echo ""
        echo "**Tweet 1 (Hook):**"
        echo "$HOOK"
        echo ""
        echo "[Make this the most scroll-stopping version possible.]"
        echo "[Use a bold claim, surprising number, or contrarian take.]"
        echo ""
        echo "---"
        echo ""
        echo "**Tweet 2 (Context):**"
        echo "Here's the backstory:"
        echo ""
        echo "[Why you did this / what problem you were solving]"
        echo "[Set the scene -- what was the situation before?]"
        echo ""
        echo "---"
        echo ""
        echo "**Tweet 3 (Process):**"
        echo "How it worked:"
        echo ""
        echo "Step 1: [first action]"
        echo "Step 2: [second action]"
        echo "Step 3: [third action]"
        echo ""
        echo "[Keep it specific and actionable.]"
        echo ""
        echo "---"
        echo ""
        echo "**Tweet 4 (Result):**"
        echo "The result:"
        echo ""
        echo "[Specific outcome with numbers]"
        echo "[What changed? What was the impact?]"
        echo ""
        echo "---"
        echo ""
        echo "**Tweet 5 (Takeaway):**"
        echo "Key lesson:"
        echo ""
        echo "[One sentence insight the reader can apply today]"
        echo ""
        echo "---"
        echo ""
        echo "**Tweet 6 (CTA):**"
        echo "I write about this stuff every week in my newsletter."
        echo ""
        echo "[Link]"
        echo ""
        echo "Follow me @[handle] for more builds like this."
        ;;

    article)
        echo "# Article / LinkedIn Long-Form Draft"
        echo ""
        echo "**Pillar:** $PILLAR_LABEL"
        echo "**Hook:** $HOOK"
        echo "**Date:** $DATE"
        echo "**Target length:** 800-1200 words"
        echo ""
        echo "---"
        echo ""
        echo "## $HOOK"
        echo ""
        echo "### The Opening (2-3 sentences)"
        echo ""
        echo "[Start with the most interesting part. Drop the reader into the middle of the action.]"
        echo "[Use a specific number, a surprising outcome, or a bold statement.]"
        echo ""
        echo "### The Problem (1-2 paragraphs)"
        echo ""
        echo "[What problem were you facing?]"
        echo "[Why does this matter to the reader?]"
        echo "[What had you tried before that didn't work?]"
        echo ""
        echo "### The Approach (2-3 paragraphs)"
        echo ""
        echo "[What did you decide to do?]"
        echo "[Walk through the key decisions and trade-offs.]"
        echo "[Include specific tools, frameworks, or techniques.]"
        echo ""
        echo "### The Result (1-2 paragraphs)"
        echo ""
        echo "[What happened? Use specific numbers.]"
        echo "[What surprised you?]"
        echo "[What would you do differently?]"
        echo ""
        echo "### The Lesson (1 paragraph)"
        echo ""
        echo "[One key takeaway the reader can apply immediately.]"
        echo "[Make it actionable -- what should they do today?]"
        echo ""
        echo "### CTA"
        echo ""
        echo "[Newsletter signup, product link, or engagement ask.]"
        echo ""
        echo "---"
        echo ""
        echo "**Voice reminders:**"
        echo "- Direct, no fluff"
        echo "- Back claims with numbers"
        echo "- Share what didn't work too"
        echo "- No guru energy"
        ;;

    post)
        echo "# Short-Form Post Draft (LinkedIn / X)"
        echo ""
        echo "**Pillar:** $PILLAR_LABEL"
        echo "**Hook:** $HOOK"
        echo "**Date:** $DATE"
        echo "**Target length:** 150-300 words"
        echo ""
        echo "---"
        echo ""
        echo "$HOOK"
        echo ""
        echo "[Expand on the hook in 2-3 sentences. Be specific.]"
        echo ""
        echo "[Share one key insight, data point, or lesson learned.]"
        echo ""
        echo "[End with a question or actionable takeaway.]"
        echo ""
        echo "---"
        echo ""
        echo "**Engagement boosters:**"
        echo "- End with a question to drive comments"
        echo "- Tag 1-2 relevant people (sparingly)"
        echo "- Use line breaks for readability"
        echo "- First line = hook (most important)"
        echo "- Include a number or specific result"
        ;;

    story)
        echo "# Story / Narrative Arc Draft"
        echo ""
        echo "**Pillar:** $PILLAR_LABEL"
        echo "**Hook:** $HOOK"
        echo "**Date:** $DATE"
        echo "**Target length:** 500-800 words"
        echo "**Format:** Newsletter section, podcast segment, or long-form social"
        echo ""
        echo "---"
        echo ""
        echo "## Act 1: The Setup"
        echo ""
        echo "[Set the scene. When and where did this happen?]"
        echo "[What was the situation? What were you trying to do?]"
        echo "[What was at stake?]"
        echo ""
        echo "## Act 2: The Conflict"
        echo ""
        echo "[What went wrong? What obstacle did you hit?]"
        echo "[What did you try first? Why didn't it work?]"
        echo "[How did it feel? Be honest about doubts and frustrations.]"
        echo ""
        echo "## Act 3: The Resolution"
        echo ""
        echo "[What did you finally try that worked?]"
        echo "[What was the turning point?]"
        echo "[Show the specific result -- numbers, outcomes, changes.]"
        echo ""
        echo "## The Lesson"
        echo ""
        echo "[What did you learn?]"
        echo "[How can the reader apply this to their situation?]"
        echo "[One sentence that captures the essence.]"
        echo ""
        echo "---"
        echo ""
        echo "**Story tips:**"
        echo "- Start in the middle of the action"
        echo "- Use specific details (dates, numbers, names)"
        echo "- Be vulnerable about failures -- it builds trust"
        echo "- The lesson should feel earned, not preachy"
        echo "- End with forward momentum, not just reflection"
        ;;
esac
