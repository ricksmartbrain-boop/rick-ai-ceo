#!/usr/bin/env bash
set -euo pipefail

# newsletter-write.sh — Generate a newsletter draft with hard memory check.
#
# The LLM emits a JSON object: {subject, topics, cta, body_md}. We save the
# JSON as the canonical draft (overlap-checked by runtime.newsletter_memory)
# and a sibling .md for human review.
#
# Theme rotation lives in runtime.newsletter_memory.THEMES (slot_for_issue).
# --theme=auto picks the next slot from the ledger.

RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
RICK_PUBLIC_AUTHOR="${RICK_PUBLIC_AUTHOR:-Rick}"
RICK_BRAND_BLURB="${RICK_BRAND_BLURB:-AI-first operator building toward $100K/month with autonomous systems.}"
DRAFTS_DIR="$RICK_DATA_ROOT/projects/email/newsletter-drafts"
WORKSPACE_ROOT="${RICK_WORKSPACE_ROOT:-$HOME/.openclaw/workspace}"

usage() {
  cat <<EOF
Usage: newsletter-write.sh [OPTIONS]

Generate a newsletter draft with hard memory check against the last 6 issues.

Options:
  --topic <text>                       Topic (optional with --auto/--theme)
  --product <name>                     Soft CTA product (optional)
  --theme <name|auto>                  Theme slot (default: auto). One of:
                                         proof-receipts, lesson-failure,
                                         tactical-playbook, behind-the-scenes,
                                         contrarian-take, tools-stack-reveal,
                                         auto
  --tone <insight|story|tactical|announcement>  Writing tone (default: insight)
  --length <short|medium|long>         Target length (default: medium)
  --auto                               Cron mode: theme=auto + topic seeded
                                       from theme.
  -h, --help                           Show this help

Output:
  \$RICK_DATA_ROOT/projects/email/newsletter-drafts/YYYY-MM-DD-<theme>.json
  \$RICK_DATA_ROOT/projects/email/newsletter-drafts/YYYY-MM-DD-<theme>.md

Exit codes:
  0  draft written, overlap-clean
  1  invalid input or LLM/JSON failure
  2  draft rejected — overlap with last 6 issues (rerun)
EOF
  exit 0
}

TOPIC=""
PRODUCT=""
THEME="auto"
TONE="insight"
LENGTH="medium"
AUTO_MODE=0

if [[ $# -eq 0 ]]; then usage; fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --topic) TOPIC="$2"; shift 2 ;;
    --product) PRODUCT="$2"; shift 2 ;;
    --theme) THEME="$2"; shift 2 ;;
    --tone) TONE="$2"; shift 2 ;;
    --length) LENGTH="$2"; shift 2 ;;
    --auto) AUTO_MODE=1; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
done

# Resolve theme=auto via runtime helper (single source of truth).
if [[ "$THEME" == "auto" ]]; then
  THEME="$(cd "$WORKSPACE_ROOT" && python3 -m runtime.newsletter_memory next-theme)"
  echo "→ theme picker auto-resolved → '$THEME'" >&2
fi

case "$THEME" in
  proof-receipts|lesson-failure|tactical-playbook|behind-the-scenes|contrarian-take|tools-stack-reveal) ;;
  *)
    echo "Error: --theme must be one of: proof-receipts, lesson-failure, tactical-playbook, behind-the-scenes, contrarian-take, tools-stack-reveal, auto" >&2
    exit 1 ;;
esac

case "$TONE" in
  insight|story|tactical|announcement) ;;
  *) echo "Error: --tone invalid" >&2; exit 1 ;;
esac

case "$LENGTH" in
  short|medium|long) ;;
  *) echo "Error: --length invalid" >&2; exit 1 ;;
esac

# Theme guidance — what each slot asks the writer to produce.
case "$THEME" in
  proof-receipts)
    THEME_GUIDE="Show receipts. Real numbers from this period, what shipped, what landed. Concrete and specific. No vague gestures." ;;
  lesson-failure)
    THEME_GUIDE="Tell one failure mode and the exact fix. Be specific about what broke and why. The lesson is what readers can avoid in their own work." ;;
  tactical-playbook)
    THEME_GUIDE="Hand the reader a playbook they can copy this week. Step-by-step, concrete, no hand-waving. Assume they want to ship by Friday." ;;
  behind-the-scenes)
    THEME_GUIDE="Show what running an autonomous system actually looks like. The boring parts, the weird parts, what an outsider would not guess. No hype." ;;
  contrarian-take)
    THEME_GUIDE="Take a position that splits the audience. Make a claim most builders would push back on. Defend it with one sharp argument and a piece of evidence." ;;
  tools-stack-reveal)
    THEME_GUIDE="Reveal one or two tools currently load-bearing in Rick's stack. Why they were chosen, what they replaced, what trade-off they imposed." ;;
esac

# Topic seed if --auto and no --topic given.
if [[ -z "$TOPIC" ]]; then
  if (( AUTO_MODE )); then
    case "$THEME" in
      proof-receipts) TOPIC="What Rick actually shipped this week (with receipts)" ;;
      lesson-failure) TOPIC="One thing Rick broke this week and how it got fixed" ;;
      tactical-playbook) TOPIC="A playbook from Rick's last 7 days you can copy" ;;
      behind-the-scenes) TOPIC="What running an autonomous AI CEO actually looks like" ;;
      contrarian-take) TOPIC="An opinion from running Rick that most builders would disagree with" ;;
      tools-stack-reveal) TOPIC="One tool Rick is using right now, and what it replaced" ;;
    esac
    echo "→ auto-mode topic seed: '$TOPIC'" >&2
  else
    echo "Error: --topic is required (or use --auto for a theme-based seed)" >&2
    exit 1
  fi
fi

case "$LENGTH" in
  short)  WORD_GUIDE="400-600 words" ;;
  medium) WORD_GUIDE="800-1200 words" ;;
  long)   WORD_GUIDE="1500-2500 words" ;;
esac

case "$TONE" in
  insight)      TONE_GUIDE="analytical and thought-provoking, share unique perspectives and data-backed observations" ;;
  story)        TONE_GUIDE="narrative and personal, tell a story from experience with lessons learned" ;;
  tactical)     TONE_GUIDE="practical and actionable, step-by-step advice readers can implement today" ;;
  announcement) TONE_GUIDE="exciting and direct, announce something new with clear value proposition" ;;
esac

NO_REPEAT_BLOCK="$(cd "$WORKSPACE_ROOT" && python3 -m runtime.newsletter_memory no-repeat-block)"

PRODUCT_CTA=""
if [[ -n "$PRODUCT" ]]; then
  PRODUCT_CTA="The CTA may softly mention the product '$PRODUCT' if natural; weave it into the narrative — never sales-y."
fi

PROMPT="$NO_REPEAT_BLOCK

---

You are writing a newsletter edition for ${RICK_PUBLIC_AUTHOR}. ${RICK_BRAND_BLURB}

Topic: $TOPIC
Theme this issue: $THEME — $THEME_GUIDE
Style: $TONE_GUIDE
Length: $WORD_GUIDE

${PRODUCT_CTA}

OUTPUT FORMAT — emit ONLY a single JSON object with these exact keys, no prose around it:
{
  \"subject\": \"<email subject line, max ~70 chars, must NOT match any subject in DO NOT REPEAT>\",
  \"topics\": [\"<3-6 short topic phrases summarizing the body>\"],
  \"cta\": \"<one short call-to-action line; must NOT verbatim-equal any CTA in DO NOT REPEAT>\",
  \"body_md\": \"<full markdown body, first-person, conversational but authoritative; H1 title, H2 sections, ~$WORD_GUIDE; numbers in body must be specific to this issue and avoid the off-limits numbers in DO NOT REPEAT>\"
}

Constraints:
- Output MUST be valid JSON — no leading/trailing prose, no code fences.
- Hook (first sentence/clause of subject) must NOT match any hook in DO NOT REPEAT.
- Numbers in body must be drawn from this week — do not recycle the off-limits numbers."

mkdir -p "$DRAFTS_DIR"
DATE=$(date +%Y-%m-%d)
OUTPUT_JSON="${DRAFTS_DIR}/${DATE}-${THEME}.json"
OUTPUT_MD="${DRAFTS_DIR}/${DATE}-${THEME}.md"
RAW_LLM_OUT="${DRAFTS_DIR}/${DATE}-${THEME}.raw.txt"

# LLM call — prefer claude CLI; fall back to anthropic CLI; else save prompt.
if command -v claude &>/dev/null; then
  echo "→ generating draft via claude CLI (theme=$THEME)..."
  claude --print "$PROMPT" > "$RAW_LLM_OUT"
elif command -v anthropic &>/dev/null; then
  echo "→ generating draft via anthropic CLI (theme=$THEME)..."
  anthropic messages create \
    --model claude-sonnet-4-6 \
    --max-tokens 8192 \
    -m "user:$PROMPT" \
    --no-stream \
    | jq -r '.content[0].text' > "$RAW_LLM_OUT"
else
  echo "Warning: no claude/anthropic CLI found — writing prompt template only." >&2
  cat > "$OUTPUT_MD" <<TEMPLATE
<!-- DRAFT PROMPT — Run through Claude manually -->
<!-- Theme: $THEME | Topic: $TOPIC | Tone: $TONE | Length: $LENGTH -->

$PROMPT
TEMPLATE
  echo "Draft prompt saved (no LLM): $OUTPUT_MD" >&2
  exit 1
fi

# Strip code fences if the model added them, then validate JSON.
python3 - "$RAW_LLM_OUT" "$OUTPUT_JSON" "$OUTPUT_MD" <<'PY'
import json, re, sys, pathlib
raw_path, json_path, md_path = sys.argv[1:4]
text = pathlib.Path(raw_path).read_text(encoding="utf-8").strip()
# Strip ```json ... ``` fences if present.
m = re.search(r"^```(?:json)?\s*\n(.*?)\n```\s*$", text, flags=re.DOTALL)
if m:
    text = m.group(1).strip()
try:
    obj = json.loads(text)
except json.JSONDecodeError as exc:
    print(f"LLM did not emit valid JSON: {exc}", file=sys.stderr)
    print("Raw output preserved at:", raw_path, file=sys.stderr)
    sys.exit(1)
required = {"subject", "topics", "cta", "body_md"}
missing = required - obj.keys()
if missing:
    print(f"LLM JSON missing keys: {sorted(missing)}", file=sys.stderr)
    sys.exit(1)
pathlib.Path(json_path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
md_lines = []
md_lines.append(f"<!-- subject: {obj['subject']} -->")
md_lines.append(f"<!-- topics: {' | '.join(obj.get('topics') or [])} -->")
md_lines.append(f"<!-- cta: {obj['cta']} -->")
md_lines.append("")
md_lines.append(obj["body_md"].strip())
md_lines.append("")
md_lines.append("---")
md_lines.append("")
md_lines.append(f"**CTA:** {obj['cta']}")
pathlib.Path(md_path).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
PY

echo "→ draft JSON: $OUTPUT_JSON"
echo "→ draft MD:   $OUTPUT_MD"

# Hard memory check
echo "→ running overlap check against last 6 issues..."
if ! (cd "$WORKSPACE_ROOT" && python3 -m runtime.newsletter_memory check "$OUTPUT_JSON"); then
  echo "" >&2
  echo "❌ Draft REJECTED — overlap with prior issues. Files preserved:" >&2
  echo "   $OUTPUT_JSON" >&2
  echo "   $OUTPUT_MD" >&2
  echo "Rerun newsletter-write.sh to regenerate." >&2
  exit 2
fi

echo "✓ overlap-clean — ready for operator review"
