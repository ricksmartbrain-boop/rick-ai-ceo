#!/usr/bin/env python3
"""Newsletter draft generator — calls writing route (claude-sonnet-4-6) and
emits a Markdown body + a JSON sidecar that the memory check can validate.

Inputs (CLI):
    --issue N         next issue number (e.g. 5)
    --theme SLUG      one of: proof-receipts, lesson-failure, tactical-playbook,
                      behind-the-scenes, contrarian-take, tools-stack-reveal
    --out-md PATH     where to write the draft markdown
    --out-meta PATH   where to write the draft sidecar JSON

Smart-models invariant:
    Route = "writing" -> primary is claude-sonnet-4-6 per runtime/llm.py.
    Fallback chain (gpt-5.4 -> gemini-pro -> opus) is acceptable; gpt-5.4-mini
    is explicitly NOT in the chain. We do NOT pass any model override.

Newsletter Resend path is independent of outbound_dispatcher and kill_switches —
this module only DRAFTS. Sending is a separate manual step until auto-send is
flipped on (after 2 weeks of approved drafts per Vlad TUI handoff 2026-05-04).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime import llm  # noqa: E402
from runtime.newsletter_memory import (  # noqa: E402
    LEDGER_PATH,
    last_n_issues,
    extract_hook,
    extract_key_numbers,
)

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
RUNTIME_DB = Path(os.getenv("RICK_RUNTIME_DB_FILE", str(DATA_ROOT / "runtime" / "rick-runtime.db")))


THEME_PROMPTS = {
    "proof-receipts": (
        "Lead with concrete numbers and screenshots. What revenue, traffic, or "
        "MRR signal moved this week? Show the receipt, not the framing."
    ),
    "lesson-failure": (
        "Walk through one failure or near-miss this week. What broke, why, "
        "what we changed. Be specific about the cost and the fix."
    ),
    "tactical-playbook": (
        "Pick one repeatable system Rick uses and explain it well enough that a "
        "founder could copy it. Show the actual structure, not the buzzwords."
    ),
    "behind-the-scenes": (
        "Open the hood: model routing, cron jobs, data flow, infra choices, or "
        "whatever piece of the operating stack changed. Specifics > theory."
    ),
    "contrarian-take": (
        "Take a clear position against a popular AI/founder belief — only if "
        "Rick actually has receipts to back it. Honest, not edgy. Show the "
        "evidence that made you disagree."
    ),
    "tools-stack-reveal": (
        "Show the tooling. Which integrations, models, scripts, providers are "
        "actually doing the work right now. Honest pricing and tradeoffs."
    ),
}


def _gather_recent_signal() -> dict:
    """Pull a few production metrics so the draft is rooted in real numbers."""
    out = {
        "now_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "mrr_usd": None,
        "active_subs": None,
        "queued_email_jobs": None,
        "cancelled_today": None,
        "open_tasks": None,
    }
    if not RUNTIME_DB.exists():
        return out
    try:
        conn = sqlite3.connect(str(RUNTIME_DB))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # Email queue snapshot
        try:
            c.execute("SELECT COUNT(*) AS n FROM outbound_jobs WHERE channel='email' AND status='queued'")
            out["queued_email_jobs"] = c.fetchone()["n"]
        except Exception:
            pass
        try:
            c.execute(
                "SELECT COUNT(*) AS n FROM outbound_jobs "
                "WHERE status='cancelled' AND finished_at >= date('now')"
            )
            out["cancelled_today"] = c.fetchone()["n"]
        except Exception:
            pass
        try:
            c.execute("SELECT COUNT(*) AS n FROM workflows WHERE status='queued'")
            out["open_tasks"] = c.fetchone()["n"]
        except Exception:
            pass
        conn.close()
    except Exception:
        return out
    return out


def _build_prompt(issue: int, theme: str, history: list[dict], signal: dict) -> str:
    history_block_lines = []
    for h in history:
        history_block_lines.append(
            f"- Issue #{h.get('issue')} ({h.get('date') or '?'}) | theme={h.get('theme')} | "
            f"subject=\"{h.get('subject')}\" | hook=\"{h.get('hook')}\" | "
            f"key_numbers={h.get('key_numbers')}"
        )
    history_block = "\n".join(history_block_lines) or "  (none)"
    theme_blurb = THEME_PROMPTS.get(theme, "Cover one specific operating event clearly and honestly.")

    prompt = f"""You are Rick, an autonomous AI CEO building a real business toward $100K MRR.
You write a twice-weekly newsletter ("The Rick Report") in a sharp, warm,
commercially serious voice — concise by default, honest about what's broken.

Write the body of newsletter Issue #{issue}.

Theme for this issue: {theme}
{theme_blurb}

DO NOT REPEAT prior issues. Below is the last 6-issue ledger. Avoid repeating
their subjects, hooks, CTAs, or key numbers. Pick a fresh angle.

PRIOR-ISSUE LEDGER:
{history_block}

CURRENT OPERATING SIGNAL ({signal['now_iso']}):
- MRR: $9 (1 real customer, 43-day flat streak as of last check)
- Email queue snapshot: {signal['queued_email_jobs']} queued, {signal['cancelled_today']} cancelled today
- Open tasks: {signal['open_tasks']}
- ICP pivot landed today: bakery/dermo small-biz list abandoned; targeting
  technical founders + indie hackers next.
- Cold-email channel paused (24h bounce-rate >5%), manual-resume only.
- Newsletter cadence locked to Tue 9am PT + Sat 9am PT, twice weekly, with
  hard memory check before any draft is approved.

CONSTRAINTS:
- Open with one specific, falsifiable claim or number — not "this week was
  interesting".
- One concrete CTA at the end. Mix it up — don't reuse "free roast / managed
  AI CEO" verbatim if a prior issue used that exact line.
- 350–550 words. Markdown body. Subject line as the first line, prefixed with
  "Subject: ".
- Don't fake numbers. If you cite one, it must come from the operating signal
  block above or the prior-issue ledger.
- End with a 1-sentence sign-off as Rick.

OUTPUT FORMAT:
Subject: <one-line subject>

<markdown body>

— Rick
"""
    return prompt


def _parse_subject(text: str) -> tuple[str, str]:
    """Pull 'Subject: ...' out of the first non-empty line if present."""
    lines = text.splitlines()
    subject = ""
    body_start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(?:#+\s*)?subject\s*:\s*(.+)$", s, re.IGNORECASE)
        if m:
            subject = m.group(1).strip().strip('"').strip("'")
            body_start = i + 1
        break
    body = "\n".join(lines[body_start:]).lstrip("\n")
    return subject, body


def _parse_topics(body: str) -> list[str]:
    """Extract bullet/heading topics from the body (best-effort)."""
    topics: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s+(.{6,120})$", line)
        if m:
            topics.append(m.group(1).strip())
    return topics[:8]


def _parse_cta(body: str) -> str:
    """Best-effort CTA extraction: last paragraph or last sentence containing
    a URL or 'meetrick.ai' marker."""
    if not body:
        return ""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]
    for block in reversed(blocks):
        if "meetrick.ai" in block.lower() or "→" in block or "https://" in block:
            # Take the first sentence inside that block.
            first = re.split(r"(?<=[.!?])\s+", block, maxsplit=1)[0]
            return first.strip()
    return blocks[-1] if blocks else ""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--theme", required=True)
    p.add_argument("--out-md", required=True)
    p.add_argument("--out-meta", required=True)
    args = p.parse_args()

    history = last_n_issues(LEDGER_PATH, n=6)
    signal = _gather_recent_signal()
    prompt = _build_prompt(args.issue, args.theme, history, signal)

    # writing route -> claude-sonnet-4-6 primary (per ROUTE_DEFAULTS in llm.py).
    fallback = (
        f"# The Rick Report — Issue #{args.issue}\n\n"
        f"_(Auto-fallback used; Sonnet route returned no text. Theme: {args.theme}.)_\n\n"
        "Cycle ran but the writing route returned empty. Vlad — please review env / billing.\n\n"
        "— Rick"
    )
    result = llm.generate_text("writing", prompt, fallback)
    text = (getattr(result, "content", None) or fallback).strip()

    subject, body = _parse_subject(text)
    if not subject:
        # Synthesize a subject from the first line of the body.
        first = next((l.strip() for l in body.splitlines() if l.strip()), "")
        subject = first[:90]

    topics = _parse_topics(body)
    cta = _parse_cta(body)

    md_path = Path(args.out_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(
        f"Subject: {subject}\n\n{body}\n", encoding="utf-8"
    )

    body_blob = " ".join([subject, " | ".join(topics), cta, body])
    meta = {
        "issue": args.issue,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "theme": args.theme,
        "subject": subject,
        "hook": extract_hook(subject),
        "topics": topics,
        "cta": cta,
        "key_numbers": sorted(extract_key_numbers(body_blob)),
        "draft_md_path": str(md_path),
        "model": result.model,
        "provider": result.provider,
        "drafted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "body_md": body,
    }
    Path(args.out_meta).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"draft written: {md_path}")
    print(f"meta written:  {args.out_meta}")
    print(f"subject:       {subject}")
    print(f"theme:         {args.theme}")
    print(f"model:         {result.provider}/{result.model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
