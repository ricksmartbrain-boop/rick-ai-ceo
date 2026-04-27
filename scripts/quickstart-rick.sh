#!/usr/bin/env sh
set -eu

usage() {
  cat <<'USAGE'
Usage:
  sh quickstart-rick.sh <api_key>
  ANTHROPIC_API_KEY=... sh quickstart-rick.sh
  OPENAI_API_KEY=... sh quickstart-rick.sh

Runs a no-side-effects Rick demo:
- 1 fixture inbound email
- 1 smart-model pass (Anthropic Opus 4.7 or OpenAI gpt-5.4)
- classification, draft reply, cold email opener, meme prompt
- no DB writes, no sends, no LaunchAgents
USAGE
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

API_KEY="${1:-${ANTHROPIC_API_KEY:-${OPENAI_API_KEY:-}}}"
if [ -z "$API_KEY" ]; then
  echo "ERROR: pass an Anthropic or OpenAI API key as argv[1] or set ANTHROPIC_API_KEY / OPENAI_API_KEY" >&2
  exit 1
fi

PROVIDER="${RICK_QUICKSTART_PROVIDER:-}"
case "$PROVIDER" in
  anthropic|openai) ;;
  *) PROVIDER="" ;;
esac

if [ -z "$PROVIDER" ]; then
  case "$API_KEY" in
    sk-ant-*) PROVIDER="anthropic" ;;
    sk-*) PROVIDER="openai" ;;
  esac
fi

if [ -z "$PROVIDER" ]; then
  PROVIDER="openai"
fi

export RICK_QS_PROVIDER="$PROVIDER"
export RICK_QS_API_KEY="$API_KEY"

python3 - <<'PY'
import json
import os
import re
import sys
import time
import textwrap
import urllib.error
import urllib.request

fixture = {
    "from": "Maya Chen <maya@northstarstudio.co>",
    "subject": "Can you show me Rick before I install him?",
    "body": (
        "I keep hearing that Rick can run founder follow-up, draft replies, and triage inboxes. "
        "Before I install anything, I want to see him do the work on my machine first. "
        "If the demo is real and fast, I’m in. If it’s fluff, I’m out."
    ),
}

prompt = f"""You are Rick, an autonomous AI CEO doing a zero-side-effect demo.

Analyze this fixture inbound email and return ONLY strict JSON with these keys:
- classification: short label for the email type
- confidence: a number or short phrase
- reply_draft: a concise, useful reply Rick would send
- cold_email_opener: one punchy opener Rick could use in a cold email
- meme_prompt: a single prompt for generating a meme about this situation
- one_line_takeaway: one sentence about what Rick just demonstrated

Rules:
- Be specific and commercially useful.
- Keep reply_draft under 120 words.
- Keep cold_email_opener under 30 words.
- Keep meme_prompt vivid and absurdly shareable.
- No markdown, no backticks, no commentary outside JSON.

Fixture email:
From: {fixture['from']}
Subject: {fixture['subject']}
Body: {fixture['body']}"""

provider = os.environ.get("RICK_QS_PROVIDER", "openai")
api_key = os.environ["RICK_QS_API_KEY"]
start = time.monotonic()


def call_anthropic() -> str:
    payload = {
        "model": "claude-opus-4-7",
        "max_tokens": 1200,
        "temperature": 0.25,
        "system": "You are Rick: sharp, warm, commercially serious, and concise.",
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "rick-quickstart/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=55) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    parts = []
    for block in body.get("content", []):
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts).strip()


def call_openai() -> str:
    payload = {
        "model": "gpt-5.4",
        "input": prompt,
        "instructions": "You are Rick: sharp, warm, commercially serious, and concise.",
        "max_output_tokens": 1200,
        "temperature": 0.25,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "rick-quickstart/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=55) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if isinstance(body.get("output_text"), str) and body["output_text"].strip():
        return body["output_text"].strip()
    parts = []
    for item in body.get("output", []):
        if not isinstance(item, dict):
            continue
        for block in item.get("content", []):
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
    return "\n".join(parts).strip()


def maybe_fallback(provider_name: str, exc: urllib.error.HTTPError):
    body = exc.read().decode("utf-8", "ignore") if hasattr(exc, "read") else ""
    alternate = "openai" if provider_name == "anthropic" else "anthropic"
    alternate_key = os.environ.get("OPENAI_API_KEY", "") if alternate == "openai" else os.environ.get("ANTHROPIC_API_KEY", "")

    if provider_name == "anthropic" and alternate_key and "credit balance is too low" in body.lower():
        return alternate, alternate_key, "Anthropic credits low; falling back to OpenAI."

    raise exc


def extract_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"model did not return JSON: {text[:300]}")
    return json.loads(cleaned[start:end + 1])


def color(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def box(title: str, lines: list[str]) -> None:
    width = 78
    print(color("┌" + "─" * width + "┐", "38;5;245"))
    print(color(f"│ {title.ljust(width - 1)}│", "1;36"))
    print(color("├" + "─" * width + "┤", "38;5;245"))
    for line in lines:
        for chunk in line.split("\n"):
            wrapped = textwrap.wrap(chunk, width=width - 1) or [""]
            for piece in wrapped:
                print(color(f"│ {piece.ljust(width - 1)}│", "37"))
    print(color("└" + "─" * width + "┘", "38;5;245"))


try:
    try:
        raw = call_anthropic() if provider == "anthropic" else call_openai()
    except urllib.error.HTTPError as exc:
        if provider == "anthropic":
            provider, api_key, note = maybe_fallback(provider, exc)
            os.environ["RICK_QS_PROVIDER"] = provider
            os.environ["RICK_QS_API_KEY"] = api_key
            print(note, file=sys.stderr)
            raw = call_openai()
        else:
            raise
    data = extract_json(raw)
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", "ignore") if hasattr(exc, "read") else ""
    print(f"ERROR: API call failed ({exc.code}): {body[:500]}", file=sys.stderr)
    raise SystemExit(1)
except Exception as exc:
    print(f"ERROR: quickstart failed: {exc}", file=sys.stderr)
    raise SystemExit(1)

elapsed = time.monotonic() - start
model_name = "claude-opus-4-7" if provider == "anthropic" else "gpt-5.4"
provider_label = "Anthropic" if provider == "anthropic" else "OpenAI"

print()
print(color("╭" + "─" * 78 + "╮", "38;5;245"))
print(color(f"│ Rick quickstart demo — {provider_label} / {model_name}".ljust(79) + "│", "1;32"))
print(color(f"│ Fixture: {fixture['subject'][:64]}".ljust(79) + "│", "37"))
print(color(f"│ Side effects: none · elapsed: {elapsed:.1f}s".ljust(79) + "│", "37"))
print(color("╰" + "─" * 78 + "╯", "38;5;245"))
print()

box("Inbound email", [f"From: {fixture['from']}", f"Subject: {fixture['subject']}", f"Body: {fixture['body']}"])
box("What Rick classified", [str(data.get("classification", "(missing)")), f"Confidence: {data.get('confidence', '(missing)')}", f"Takeaway: {data.get('one_line_takeaway', '(missing)')}"])
box("Draft reply", [str(data.get("reply_draft", "(missing)"))])
box("Cold email opener", [str(data.get("cold_email_opener", "(missing)"))])
box("Meme prompt", [str(data.get("meme_prompt", "(missing)"))])

print()
print(color("Want this running 24/7? Run: ./install-rick.sh", "1;33"))
print()
PY
