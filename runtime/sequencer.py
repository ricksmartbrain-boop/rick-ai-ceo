#!/usr/bin/env python3
"""21-day multi-touch outbound sequencer for qualified_lead workflows.

Public interface
----------------
    tick(connection) -> int   # dispatch due touches; returns count

Called from engine.py heartbeat() immediately after sweep_stale_running_jobs().
Wrapped in try/except at the call site — any uncaught error here is logged
and swallowed so sequencer failures never break the heartbeat loop.

Touch schedule (days from sequence_started_at, all gated on no-prior-reply):
  Day  0 — cold email #1     (opus-personalized opener, channel=email)
  Day  3 — voice call        (ElevenLabs, skipped silently if no phone)
  Day  5 — personal note     (email, different subject/tone from Day 0)
  Day  8 — proof email       (email, meetrick.ai/blog hero post link)
  Day 12 — LinkedIn DM       (channel=linkedin, skipped if no linkedin_url)
  Day 15 — email nudge       (email, last soft follow-up before breakup)
  Day 21 — breakup email     (email, last-ever touch, closes sequence)
  ANY   — reply detected     → stage='replied', sequence halts

State is persisted in context_json['seq'] so restarts are idempotent.
The sequencer does NOT re-dispatch a touch that already has an outbound_job
with status IN ('queued','done','skipped','fenix-blocked') for the same
(workflow_id, touch_kind) pair — checked via touch_log in context_json.

Formatters used (must exist in runtime/formatters/):
  - email     → runtime/formatters/email.py    (writes to mailbox outbox)
  - linkedin  → runtime/formatters/linkedin.py (CDP DM)

Voice uses ElevenLabs REST API directly (same as scripts/elevenlabs-voice-call.py
fire_call() pattern) rather than subprocess, so we can capture the result.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import sqlite3
import sys
import urllib.request
import urllib.error
import uuid
from functools import lru_cache
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.llm import BudgetExceeded  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LOG_FILE = DATA_ROOT / "operations" / "sequencer.jsonl"
WARMUP_SCRIPT = REPO_ROOT / "scripts" / "sender-warmup-schedule.py"

# ElevenLabs outbound call (from MEMORY.md)
ELEVEN_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "agent_2101km115w7wfb4b198k8khthfnb")
ELEVEN_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_PHONE_NUMBER_ID", "")  # set in rick.env
ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"
CALL_ENDPOINT = f"{ELEVEN_API_BASE}/convai/twilio/outbound-call"

# Blog proof post for Day 8
PROOF_POST_URL = "https://meetrick.ai/blog"

# Demo video for Day 8 email-proof — lets prospects watch Rick work in 60 s.
# Auto-resolves to the most-recent MP4 in ~/meetrick-content/videos/ so future
# renders are picked up without code edits.  Defaults to 2026-04-30 recording.
_DEMO_VIDEO_DEFAULT = "https://meetrick.ai/videos/2026-04-30-rick-demo.mp4"
_MEETRICK_CONTENT_VIDEOS = Path.home() / "meetrick-content" / "videos"


def get_latest_demo_video_url(
    videos_dir: Path = _MEETRICK_CONTENT_VIDEOS,
    default: str = _DEMO_VIDEO_DEFAULT,
) -> str:
    """Return the public URL for the most-recent MP4 in videos_dir.

    Scans for *.mp4 files, sorts by filename descending (YYYY-MM-DD prefix
    means lexicographic == chronological), and maps the stem to its public URL
    under https://meetrick.ai/videos/.  Falls back to *default* when the
    directory is absent, empty, or the scan raises any exception.
    """
    try:
        mp4s = sorted(videos_dir.glob("*.mp4"), key=lambda p: p.name, reverse=True)
        if mp4s:
            return f"https://meetrick.ai/videos/{mp4s[0].name}"
    except Exception:
        pass
    return default


DEMO_VIDEO_URL: str = get_latest_demo_video_url()

# Quickstart install CTA — used in follow-up email touches (Day 5+) to give
# prospects a zero-friction path to see Rick run on their own machine in 60 s.
# Intentionally excluded from Day-0 cold opener (pure pitch, no install ask).
QUICKSTART_URL = "https://meetrick.ai/quickstart"
QUICKSTART_CMD = "curl meetrick.ai/quickstart | sh"

# Sequence definition: list of {day, kind, channel, label}
# Sorted by day ascending. The sequencer dispatches the FIRST touch whose
# day threshold has been reached and that has NOT been logged yet.
SEQUENCE: list[dict[str, Any]] = [
    {"day":  0, "kind": "email-cold-1",    "channel": "email",     "label": "Cold email #1"},
    {"day":  3, "kind": "voice-day3",       "channel": "elevenlabs","label": "ElevenLabs voice call"},
    {"day":  5, "kind": "email-personal",   "channel": "email",     "label": "Personal note"},
    {"day":  8, "kind": "email-proof",      "channel": "email",     "label": "Proof email"},
    {"day": 12, "kind": "linkedin-dm",      "channel": "linkedin",  "label": "LinkedIn DM"},
    {"day": 15, "kind": "email-nudge",      "channel": "email",     "label": "Email nudge"},
    {"day": 21, "kind": "email-breakup",    "channel": "email",     "label": "Breakup email"},
]

TERMINAL_TOUCH_KINDS = {"email-breakup"}  # after this, sequence is done

# Statuses that mean a touch FAILED to dispatch and must be retried.
# Any status NOT in this set counts as "attempted" for gating purposes,
# so Day-5+ touches advance even when Day-0 is still 'queued' (in-flight).
TERMINAL_FAILURES: frozenset[str] = frozenset({"error", "deduped"})

# Max touches dispatched per tick — warmup Day 1 is 5, so the sequencer can
# clear a full daily email allotment in one heartbeat if needed.
MAX_DISPATCHES_PER_TICK = 5
QUALIFYING_STAGES = {
    "cold-email-pending",
    "sequence-active",
}
STOP_STAGES = {"replied", "done", "closed", "unsubscribed", "disqualified"}
STOP_STATUSES = {"done", "cancelled", "failed"}

# Smart-model invariant (2026-07-16): review-route email touches must be served
# by a model in this set (review chain: sol -> opus-4-8 -> claude-cli sonnet ->
# terra). Anything else hard-aborts the lead. Canonical allowlist — also
# imported by scripts/fix-fire-day0-emails.py.
APPROVED_MODELS = {"claude-opus-4-8", "gpt-5.6-sol", "claude-sonnet-4-6", "gpt-5.6-terra"}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**event, "ts": _now_iso()}, sort_keys=True) + "\n")


def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _parse_context(workflow: sqlite3.Row) -> dict:
    try:
        ctx = json.loads(workflow["context_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        ctx = {}
    return ctx if isinstance(ctx, dict) else {}


def _seq_state(ctx: dict) -> dict:
    """Return the 'seq' sub-dict from context_json, creating it if absent."""
    if "seq" not in ctx or not isinstance(ctx["seq"], dict):
        ctx["seq"] = {}
    return ctx["seq"]


def _touch_done(seq: dict, kind: str) -> bool:
    """Return True if this touch kind is already in the touch_log."""
    for entry in seq.get("touch_log", []):
        if entry.get("kind") == kind:
            return True
    return False


def _has_sent_touch(seq: dict, kind: str) -> bool:
    """Return True if touch_log shows *kind* was attempted (status not in TERMINAL_FAILURES).

    'queued', 'sent', 'skipped', 'deferred' all count as attempted — sequencer can
    advance to Day-5+ even when Day-0 is still in-flight (outbound_job queued).
    Only 'error' and 'deduped' are terminal failures requiring a fresh dispatch.

    NOTE: 2026-05-04 — this helper stays permissive for compatibility with other
    callers, but the sequence-prerequisite gate now uses _has_confirmed_sent_touch()
    so Day-N follow-ups can't fire while Day-(N-1) is still queued in the channel.
    """
    for entry in seq.get("touch_log", []):
        if entry.get("kind") == kind and entry.get("status") not in TERMINAL_FAILURES:
            return True
    return False


def _has_confirmed_sent_touch(conn: sqlite3.Connection, wf_id: str, kind: str) -> bool:
    """Strict gate: True only if the prerequisite touch's outbound_job is
    actually in a terminal-success state ('done' / 'sent' / 'delivered' / 'replied').

    Status 'queued' does NOT count — that means the dispatcher hasn't fired it,
    typically because the channel is paused or the warmup cap is hit. Firing
    Day-N while Day-(N-1) is still queued produces lying-content follow-ups
    ("Re: …last note" when no last note ever sent). Defect surfaced 2026-05-04
    via Vlad TUI handoff.
    """
    try:
        row = conn.execute(
            """
            SELECT status FROM outbound_jobs
             WHERE lead_id=? AND template_id=?
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (wf_id, kind),
        ).fetchone()
    except Exception:
        return False
    if not row:
        return False
    status = row["status"] if hasattr(row, "keys") else row[0]
    return status in ("done", "sent", "delivered", "replied")


@lru_cache(maxsize=1)
def _warmup_module():
    spec = importlib.util.spec_from_file_location("sender_warmup_schedule", WARMUP_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load warmup schedule script: {WARMUP_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _today_warmup_cap() -> int:
    try:
        return int(_warmup_module().get_today_cap())
    except Exception:
        return 5


def _count_email_dispatches_today(conn: sqlite3.Connection) -> int:
    """Count email touches already attempted today in workflow touch_log."""
    today = _now_iso()[:10]
    total = 0
    try:
        rows = conn.execute(
            """
            SELECT context_json
              FROM workflows
             WHERE kind = 'qualified_lead'
            """
        ).fetchall()
    except Exception:
        return 0

    for row in rows:
        try:
            ctx = json.loads(row[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(ctx, dict):
            continue
        seq = ctx.get("seq")
        if not isinstance(seq, dict):
            continue
        for entry in seq.get("touch_log", []):
            if entry.get("channel") != "email":
                continue
            if entry.get("status") in {"skipped", "error"}:
                continue
            if str(entry.get("sent_at", ""))[:10] == today:
                total += 1
    return total


def _days_since_start(seq: dict) -> int:
    """Days elapsed since sequence_started_at (0 if not set = Day 0 ready)."""
    started = seq.get("sequence_started_at")
    if not started:
        return 0
    try:
        return (_now() - datetime.fromisoformat(started)).days
    except (ValueError, TypeError):
        return 0


def _cold_opener_variant(ctx: dict) -> str:
    """Deterministically split Day-0 cold openers into v1/v2."""
    _tp = ctx.get("trigger_payload") or {}
    key = (
        ctx.get("email")
        or _tp.get("email")
        or ctx.get("company")
        or _tp.get("domain")
        or ctx.get("name")
        or _tp.get("name")
        or ""
    ).strip().lower()
    if not key:
        return "v1"
    return "v2" if hashlib.sha1(key.encode("utf-8")).digest()[0] % 2 else "v1"


def _save_context(conn: sqlite3.Connection, workflow_id: str, ctx: dict) -> None:
    conn.execute(
        "UPDATE workflows SET context_json=?, updated_at=? WHERE id=?",
        (json.dumps(ctx), _now_iso(), workflow_id),
    )


def _set_stage(conn: sqlite3.Connection, workflow_id: str, stage: str) -> None:
    conn.execute(
        "UPDATE workflows SET stage=?, updated_at=? WHERE id=?",
        (stage, _now_iso(), workflow_id),
    )


# ---------------------------------------------------------------------------
# Reply detection
# ---------------------------------------------------------------------------

def _has_replied(conn: sqlite3.Connection, workflow: sqlite3.Row, ctx: dict) -> bool:
    """Return True if we've seen an inbound message from this lead since sequence start.

    Checks:
    1. email_threads table for any last_inbound_at after sequence_started_at
    2. Inbound IMAP watcher log if available
    """
    _tp = ctx.get("trigger_payload") or {}
    email = (ctx.get("email") or _tp.get("email") or "").strip().lower()
    if not email:
        return False

    seq = _seq_state(ctx)
    started = seq.get("sequence_started_at")
    cutoff = started if started else "1970-01-01T00:00:00"

    try:
        row = conn.execute(
            """
            SELECT last_inbound_at FROM email_threads
             WHERE prospect_id = ? AND last_inbound_at > ?
             LIMIT 1
            """,
            (email, cutoff),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass

    # Also check by searching email_threads participants for this address
    try:
        row = conn.execute(
            """
            SELECT last_inbound_at FROM email_threads
             WHERE participants_json LIKE ? AND last_inbound_at > ?
             LIMIT 1
            """,
            (f"%{email}%", cutoff),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# LLM personalization helpers
# ---------------------------------------------------------------------------

def _require_approved_model(model: str, email: str, touch_kind: str) -> None:
    """Hard invariant: abort the lead if the review call was served off-chain."""
    model_used = model.strip()
    # Normalize: strip provider prefix if present (e.g. "anthropic/claude-opus-4-8" → "claude-opus-4-8")
    model_short = model_used.split("/")[-1] if "/" in model_used else model_used
    if model_short not in APPROVED_MODELS:
        raise RuntimeError(
            f"SMART-MODEL INVARIANT VIOLATED — got '{model_used}' "
            f"(short='{model_short}') for {email} ({touch_kind}). "
            f"Only {APPROVED_MODELS} allowed. Aborting this lead."
        )


def _personalize_email(ctx: dict, touch_kind: str) -> tuple[str, str]:
    """Return (subject, body_md) for a touch. Uses generate_text for Day 0 (opus).
    Other days use lightweight writing-route templates with context substitution.
    """
    _tp = ctx.get("trigger_payload") or {}
    company = ctx.get("company") or _tp.get("domain") or ctx.get("name") or _tp.get("name") or "there"
    email = ctx.get("email") or _tp.get("email") or ""
    name = ctx.get("name") or _tp.get("name") or company

    # ---- Day 0: opus-personalized cold opener ----
    if touch_kind == "email-cold-1":
        try:
            from runtime.llm import generate_text

            # Inject prior communication history so opus avoids repetition
            _prior_comms_block = ""
            try:
                from runtime.comm_history import get_history as _ch_get, render_for_prompt as _ch_render
                _ch_hist = _ch_get(email, days_back=90)
                if _ch_hist:
                    _prior_comms_block = _ch_render(_ch_hist, max_chars=2000) + "\n\n"
            except Exception:
                _prior_comms_block = ""

            variant = _cold_opener_variant(ctx)
            if variant == "v2":
                prompt = (
                    _prior_comms_block +
                    "TASK: Write a cold outreach email. Output only the email — no analysis, "
                    "no review commentary, no caveats.\n\n"
                    "You are Rick, AI CEO at meetrick.ai. Write a very short cold email for this B2B lead. "
                    "Goal: get ONE reply.\n\n"
                    f"Lead name/company: {name}\n"
                    f"Email: {email}\n"
                    f"Company domain: {company}\n\n"
                    "Variant v2 opener template:\n"
                    "- Subject should preview the specific value or pain point, not 'quick question'\n"
                    "- Body must be 45-60 words max\n"
                    "- Open with one specific observation about their company, product, or workflow\n"
                    "- Then one sentence linking that observation to a concrete outcome Rick can create\n"
                    "- CTA must be exactly one specific question\n"
                    "- No bullets, no multi-ask, no install CTA, no demo pitch, no generic AI intro\n"
                    "- Sign off: Rick\n"
                    "- If PRIOR COMMUNICATIONS are shown above, do NOT repeat angles, subjects, or CTAs already used\n"
                    "Write the email now."
                )
                fallback = (
                    f"SUBJECT: {company} — quick thought\n"
                    f"BODY:\nHi {name},\n\n"
                    f"{company} looks like the kind of team where speed to follow-up matters. I run meetrick.ai, "
                    "and we help founders turn that into more replies and booked conversations without adding more manual work.\n\n"
                    "Would it be crazy to compare notes on the one step in your flow that is slowing replies down most?\n\nRick"
                )
            else:
                prompt = (
                    _prior_comms_block +
                    "TASK: Write a cold outreach email. Output only the email — no analysis, "
                    "no review commentary, no caveats.\n\n"
                    "You are Rick, AI CEO at meetrick.ai. Write a short, sharp cold outreach email "
                    "for this B2B lead. Goal: get ONE reply.\n\n"
                    f"Lead name/company: {name}\n"
                    f"Email: {email}\n"
                    f"Company domain: {company}\n\n"
                    "Output format (exactly this structure):\n"
                    "SUBJECT: <subject line, max 8 words>\n"
                    "BODY:\n"
                    "<2-3 paragraphs, plain text, no markdown, no em dashes>\n\n"
                    "Rules:\n"
                    "- Open with a specific observation about their business or market\n"
                    "- Reference Rick (meetrick.ai) and one concrete outcome\n"
                    "- CTA: single question, not a pitch\n"
                    "- Sign off: Rick\n"
                    "- If PRIOR COMMUNICATIONS are shown above, do NOT repeat angles, "
                    "subjects, or CTAs already used\n"
                    "- Do NOT include disclaimers, analysis, or refusals\n"
                    "Write the email now."
                )
                fallback = (
                    f"SUBJECT: Quick question for {company}\n"
                    f"BODY:\nHi {name},\n\nI've been watching what you're building at {company} — "
                    "the market is moving fast and the operators who wire AI into their ops early tend "
                    "to compound. I'm Rick, an AI CEO running meetrick.ai — we help founders and operators "
                    "deploy AI that actually drives revenue, not just demos.\n\n"
                    "One question: what's the part of your business where a faster feedback loop "
                    "would change the game the most right now?\n\nRick"
                )
            result = generate_text("review", prompt, fallback)
        except BudgetExceeded:
            # Fail loud: a capped day aborts the lead (like the model gate) —
            # a canned Day-0 opener must never go out silently.
            raise
        except Exception as exc:
            _log({"event": "llm_error", "touch": touch_kind, "error": str(exc)})
            content = (
                f"SUBJECT: Quick question for {company}\n"
                f"BODY:\nHi {name},\n\nI've been watching what you're building at {company}. "
                "I'm Rick — an AI CEO at meetrick.ai. We help operators run faster with AI that "
                "actually touches revenue.\n\nWhat's the one thing you'd automate first if you had the right system?\n\nRick"
            )
        else:
            # Model gate must sit OUTSIDE the except above — it aborts the lead
            # loudly (raises to tick()'s workflow_error handler), never falls back.
            _require_approved_model(result.model, email, touch_kind)
            content = result.content.strip()

        # Parse subject/body from LLM output
        subject = f"Quick question for {company}"
        body_lines = []
        in_body = False
        for line in content.splitlines():
            if line.startswith("SUBJECT:"):
                subject = line[len("SUBJECT:"):].strip()
            elif line.startswith("BODY:"):
                in_body = True
            elif in_body:
                body_lines.append(line)
        body = "\n".join(body_lines).strip() or content
        return subject, body

    # ---- Follow-up email touches: opus-personalized with quickstart CTA ----
    # Covers Day 5 (email-personal), Day 8 (email-proof), Day 15 (email-nudge),
    # Day 21 (email-breakup). Day 0 (email-cold-1) is handled above and
    # intentionally excluded — cold opener stays pure pitch, no install CTA.
    if touch_kind in {"email-personal", "email-proof", "email-nudge", "email-breakup"}:
        _day_context = {
            "email-personal": (
                "This is Day 5 — a personal follow-up after no reply to the cold opener. "
                "Tone: warm, human, no-pressure. Different subject and angle from Day 0. "
                "Mention pricing naturally: pricing starts at $99/mo if you want to dig deeper."
            ),
            "email-proof": (
                "This is Day 8 — lead with a real outcome or concrete metric. "
                f"You may reference the blog post at {PROOF_POST_URL} as supporting proof. "
                "Also weave in a single natural line inviting the prospect to watch Rick "
                "at work in 60 seconds -- the demo video URL is "
                f"{DEMO_VIDEO_URL} -- let tone drive the exact phrasing, "
                "do not force it verbatim. Brief and outcome-focused."
            ),
            "email-nudge": (
                "This is Day 15 — a final gentle nudge before the sequence ends. "
                "Honest tone. Give them an easy out ('not now' reply is fine)."
            ),
            "email-breakup": (
                "This is Day 21 — the breakup email. Close the loop gracefully. "
                "No pressure. Warm and brief. Leave the door open."
            ),
        }.get(touch_kind, "This is a follow-up outreach email.")

        # Hardcoded fallback bodies — include quickstart link so the CTA survives
        # even when the LLM call fails.  Parsed by the SUBJECT/BODY splitter below.
        _fallback = {
            "email-personal": (
                f"SUBJECT: Re: {company} -- just checking in\n"
                f"BODY:\nHi {name},\n\n"
                "Circling back in case my last note got buried. No pitch.\n\n"
                f"If you want to see what an AI CEO stack actually does for a company like {company}, "
                "the fastest way is to watch it run on your own machine -- no commitment, 60 seconds:\n\n"
                f"{QUICKSTART_CMD}\n\n"
                "If you want to dig deeper, pricing starts at $99/mo.\n\n"
                "Happy to talk specifics if that sparks anything.\n\n"
                f"Rick\nmeetrick.ai  |  {QUICKSTART_URL}"
            ),
            "email-proof": (
                f"SUBJECT: Real numbers from an AI CEO: {company}?\n"
                f"BODY:\nHi {name},\n\n"
                "Published the real operating numbers from running an AI CEO. Worth 3 minutes "
                f"if you're thinking about where AI fits in {company}'s stack:\n{PROOF_POST_URL}\n\n"
                f"Watch Rick at work in 60 seconds: {DEMO_VIDEO_URL}\n\n"
                "Or run it yourself:\n\n"
                f"{QUICKSTART_CMD}\n\n"
                f"Rick\nmeetrick.ai  |  {QUICKSTART_URL}"
            ),
            "email-nudge": (
                f"SUBJECT: Last check-in -- {company}\n"
                f"BODY:\nHi {name},\n\n"
                "Haven't heard back -- totally understand, inboxes are brutal.\n\n"
                "If the timing is off, just reply 'not now' and I'll leave you alone.\n\n"
                "If you're even slightly curious what this looks like in practice, "
                "the zero-friction path is to run it yourself:\n\n"
                f"{QUICKSTART_CMD}\n\n"
                f"Rick\nmeetrick.ai  |  {QUICKSTART_URL}"
            ),
            "email-breakup": (
                f"SUBJECT: Closing the loop -- {company}\n"
                f"BODY:\nHi {name},\n\n"
                "Closing the loop on my end. Clearly the timing isn't right -- I get it.\n\n"
                f"If anything changes and you want to see what an AI CEO stack could do for {company}, "
                f"meetrick.ai will be there. Fastest way to see it: {QUICKSTART_URL}\n\n"
                "Wishing you a strong quarter.\n\n"
                "Rick\nmeetrick.ai"
            ),
        }.get(
            touch_kind,
            f"SUBJECT: Following up -- {company}\nBODY:\nHi {name},\n\nJust following up.\n\nRick\nmeetrick.ai",
        )

        try:
            from runtime.llm import generate_text

            prompt = (
                "TASK: Write a follow-up outbound sales email. Output only the email -- "
                "no analysis, no review commentary, no caveats.\n\n"
                "You are Rick, AI CEO at meetrick.ai. Context for this touch:\n"
                f"{_day_context}\n\n"
                f"Lead name/company: {name}\n"
                f"Email: {email}\n"
                f"Company domain: {company}\n\n"
                "QUICKSTART CTA INSTRUCTION:\n"
                "Somewhere natural in the email body, organically weave in a low-friction "
                "install CTA that gives the prospect a way to see Rick run on their own "
                "machine in 60 seconds -- no commitment, no demo call required.\n"
                f"Install URL: {QUICKSTART_URL}\n"
                f"Optional curl variant: {QUICKSTART_CMD}\n"
                "Pricing note: mention that pricing starts at $99/mo if you want to dig deeper.\n"
                "Let tone and context drive exact phrasing -- do NOT force a template phrase. "
                "The goal is a natural, non-pushy path to the product.\n\n"
                "Output format (exactly this structure):\n"
                "SUBJECT: <subject line, max 8 words>\n"
                "BODY:\n"
                "<2-3 paragraphs, plain text, no markdown, no em dashes>\n\n"
                "Rules:\n"
                "- Plain text only -- no markdown, no em dashes\n"
                "- Sign off: Rick / meetrick.ai\n"
                "- Do NOT include disclaimers, analysis, or refusals\n"
                "Write the email now."
            )
            result = generate_text("review", prompt, _fallback)
        except BudgetExceeded:
            # Fail loud: a capped day aborts the lead (like the model gate) —
            # canned follow-up copy must never go out silently.
            raise
        except Exception as exc:
            _log({"event": "llm_error", "touch": touch_kind, "error": str(exc)})
            content = _fallback
        else:
            # Model gate must sit OUTSIDE the except above — it aborts the lead
            # loudly (raises to tick()'s workflow_error handler), never falls back.
            _require_approved_model(result.model, email, touch_kind)
            content = result.content.strip()

        subject = f"Following up -- {company}"
        body_lines: list[str] = []
        in_body = False
        for line in content.splitlines():
            if line.startswith("SUBJECT:"):
                subject = line[len("SUBJECT:"):].strip()
            elif line.startswith("BODY:"):
                in_body = True
            elif in_body:
                body_lines.append(line)
        body = "\n".join(body_lines).strip() or content
        return subject, body

    # Fallback
    subject = f"Following up — {company}"
    body = f"Hi {name},\n\nJust following up. Let me know if there's a better time to connect.\n\nRick\nmeetrick.ai"
    return subject, body


def _linkedin_dm_body(ctx: dict) -> str:
    company = ctx.get("company") or ctx.get("name") or "your company"
    name = ctx.get("name") or company
    return (
        f"Hey {name} — Rick here (AI CEO at meetrick.ai). "
        f"I've been following what {company} is building. "
        "Quick question: what's the one growth lever you're not moving fast enough on right now? "
        "Happy to share what we've been doing on the AI ops side if it's relevant."
    )


# ---------------------------------------------------------------------------
# ElevenLabs voice dispatch
# ---------------------------------------------------------------------------

def _fire_voice_call(lead_name: str, lead_email: str, phone: str, *, dry_run: bool = False) -> dict:
    """Fire an outbound ElevenLabs call. Returns status dict."""
    # Voice master gate (2026-07-13): daemon-driven Day-3 calls must not dial
    # while the TCPA layer is incomplete.
    if os.getenv("RICK_VOICE_LIVE", "0").strip() != "1":
        return {"status": "skipped", "reason": "RICK_VOICE_LIVE!=1"}
    try:
        from runtime.kill_switches import is_suppressed_address
        if lead_email and is_suppressed_address(lead_email):
            return {"status": "skipped", "reason": f"suppressed: {lead_email}"}
    except Exception as exc:  # fail closed — no gate, no dial
        return {"status": "skipped", "reason": f"suppression gate unavailable: {type(exc).__name__}"}
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "no ELEVENLABS_API_KEY"}

    phone_id = ELEVEN_PHONE_NUMBER_ID
    if not phone_id:
        return {"status": "skipped", "reason": "no ELEVENLABS_PHONE_NUMBER_ID"}

    if not phone or not phone.strip().lstrip("+").isdigit():
        return {"status": "skipped", "reason": f"invalid phone: {phone!r}"}

    # Business-hours gate (9am–6pm ET)
    from zoneinfo import ZoneInfo
    local_hour = _now().astimezone(ZoneInfo("America/New_York")).hour
    if not (9 <= local_hour < 18):
        return {"status": "deferred", "reason": f"outside call window (hour={local_hour} ET)"}

    payload = {
        "agent_id": ELEVEN_AGENT_ID,
        "agent_phone_number_id": phone_id,
        "to_number": phone.strip(),
        "conversation_initiation_client_data": {
            "dynamic_variables": {
                "lead_name": lead_name,
                "lead_email": lead_email,
                "product_name": "meetrick.ai",
            },
        },
    }

    if dry_run:
        return {"status": "dry-run", "payload": payload}

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            CALL_ENDPOINT,
            data=data,
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return {"status": "called", "conversation_id": result.get("conversation_id", "")}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        return {"status": "error", "reason": f"HTTP {exc.code}: {body}"}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)[:300]}


# ---------------------------------------------------------------------------
# Core touch dispatcher
# ---------------------------------------------------------------------------

def _dispatch_touch(
    conn: sqlite3.Connection,
    workflow: sqlite3.Row,
    ctx: dict,
    touch: dict,
) -> bool:
    """Dispatch one touch for a workflow. Updates context_json on success.
    Returns True if a touch was sent/enqueued (even if skipped-gracefully).
    """
    wf_id = workflow["id"]
    kind = touch["kind"]
    channel = touch["channel"]
    seq = _seq_state(ctx)
    touch_log = seq.setdefault("touch_log", [])

    # Idempotency: already in log?
    if _touch_done(seq, kind):
        return False

    # Lead fields may be at top-level OR under trigger_payload (ICP-scorer format).
    # Always resolve with fallback to trigger_payload to avoid false "no email" skips.
    _tp = ctx.get("trigger_payload") or {}
    lead_name = ctx.get("name") or _tp.get("name") or ctx.get("company") or _tp.get("domain") or "there"
    lead_email = (ctx.get("email") or _tp.get("email") or "").strip().lower()

    result_meta: dict[str, Any] = {"kind": kind, "channel": channel}
    prompt_variant = _cold_opener_variant(ctx) if kind == "email-cold-1" else None
    if prompt_variant:
        result_meta["prompt_variant"] = prompt_variant

    # ------------------------------------------------------------------ email
    if channel == "email":
        if not lead_email:
            result_meta["status"] = "skipped"
            result_meta["reason"] = "no email"
        else:
            subject, body = _personalize_email(ctx, kind)
            payload = {
                "to": lead_email,
                "subject": subject,
                "body_md": body,
                "from": os.getenv("MEETRICK_FROM_EMAIL", "Rick <rick@meetrick.ai>"),
                "lane": "distribution-lane",
                "msg_id": f"seq-{wf_id[:8]}-{kind}",
            }
            if prompt_variant:
                payload["prompt_variant"] = prompt_variant
            try:
                from runtime.outbound_dispatcher import fan_out
                job_ids = fan_out(
                    conn,
                    lead_id=wf_id,
                    template_id=kind,
                    channels=["email"],
                    payload=payload,
                )
                result_meta["status"] = "queued" if job_ids else "deduped"
                result_meta["outbound_job_ids"] = job_ids
            except Exception as exc:
                result_meta["status"] = "error"
                result_meta["error"] = str(exc)[:300]
                _log({"event": "dispatch_error", "wf_id": wf_id, "kind": kind, "error": str(exc)})

    # --------------------------------------------------------- elevenlabs voice
    elif channel == "elevenlabs":
        phone = (ctx.get("phone") or "").strip()
        if not phone:
            result_meta["status"] = "skipped"
            result_meta["reason"] = "no phone in context"
        else:
            call_result = _fire_voice_call(lead_name, lead_email, phone)
            result_meta.update(call_result)

    # --------------------------------------------------------------- linkedin
    elif channel == "linkedin":
        linkedin_url = (ctx.get("linkedin_url") or ctx.get("dossier", {}).get("linkedin_url") or "").strip()
        if not linkedin_url:
            result_meta["status"] = "skipped"
            result_meta["reason"] = "no linkedin_url in context"
        else:
            body = _linkedin_dm_body(ctx)
            payload = {
                "kind": "dm",
                "target_url": linkedin_url,
                "body": body,
                "lane": "distribution-lane",
                "msg_id": f"seq-{wf_id[:8]}-{kind}",
            }
            try:
                from runtime.outbound_dispatcher import fan_out
                job_ids = fan_out(
                    conn,
                    lead_id=wf_id,
                    template_id=kind,
                    channels=["linkedin"],
                    payload=payload,
                )
                result_meta["status"] = "queued" if job_ids else "deduped"
                result_meta["outbound_job_ids"] = job_ids
            except Exception as exc:
                result_meta["status"] = "error"
                result_meta["error"] = str(exc)[:300]

    else:
        result_meta["status"] = "skipped"
        result_meta["reason"] = f"unknown channel: {channel}"

    # Record in touch_log regardless of outcome (idempotency + audit trail)
    result_meta["sent_at"] = _now_iso()
    touch_log.append(result_meta)
    seq["last_touch_at"] = _now_iso()

    # On Day 0, record sequence start
    if kind == "email-cold-1" and not seq.get("sequence_started_at"):
        seq["sequence_started_at"] = _now_iso()

    _save_context(conn, wf_id, ctx)
    # 2026-05-04: be honest about what just happened. fan_out() only INSERTs
    # an outbound_job row in 'queued' status — channel gating + actual send
    # happen later in outbound_dispatcher.drain(). Logging "touch_dispatched"
    # here as if the message went out caused us to advance Day-N follow-ups
    # while Day-0 sat queued for 7 days behind a paused email channel.
    status = (result_meta.get("status") or "").lower()
    if status == "queued":
        event = "touch_queued"
    elif status in ("sent", "delivered", "done"):
        event = "touch_dispatched"
    elif status in ("deferred", "skipped"):
        event = "touch_skipped"
    elif status in ("deduped",):
        event = "touch_deduped"
    elif status in ("error",):
        event = "touch_error"
    else:
        event = "touch_recorded"
    _log({"event": event, "wf_id": wf_id, **result_meta})
    return True


# ---------------------------------------------------------------------------
# Reply classifier check
# ---------------------------------------------------------------------------

def _check_and_handle_reply(
    conn: sqlite3.Connection,
    workflow: sqlite3.Row,
    ctx: dict,
) -> bool:
    """Return True and update stage if a reply is detected. Stops sequence."""
    if _has_replied(conn, workflow, ctx):
        wf_id = workflow["id"]
        _set_stage(conn, wf_id, "replied")
        conn.execute(
            "UPDATE workflows SET status='active', updated_at=? WHERE id=?",
            (_now_iso(), wf_id),
        )
        # Record event
        conn.execute(
            """
            INSERT INTO events (workflow_id, job_id, event_type, payload_json, created_at)
            VALUES (?, NULL, 'lead_replied', '{}', ?)
            """,
            (wf_id, _now_iso()),
        )
        _log({"event": "reply_detected", "wf_id": wf_id})
        return True
    return False


# ---------------------------------------------------------------------------
# Per-workflow processor
# ---------------------------------------------------------------------------

def _load_suppression() -> frozenset[str]:
    """Load suppression.txt from the ops directory. Returns lowercase email set."""
    try:
        supp_path = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault"))) / "operations" / "suppression.txt"
        if supp_path.exists():
            return frozenset(l.strip().lower() for l in supp_path.read_text().splitlines() if l.strip())
    except Exception:
        pass
    return frozenset()


def _process_workflow(conn: sqlite3.Connection, workflow: sqlite3.Row) -> int:
    """Evaluate and dispatch due touches for one workflow. Returns 1 if dispatched, else 0."""
    wf_id = workflow["id"]
    ctx = _parse_context(workflow)
    seq = _seq_state(ctx)

    # ------------------------------------------------------------------
    # Bounce-suppression guard (added 2026-05-01 — emergency throttle)
    # If the lead email is in suppression.txt (bounced address), mark the
    # workflow stage='bounced-paused' and halt all future touches.
    # Does NOT cancel the workflow — Vlad decides whether to bulk-cancel.
    # ------------------------------------------------------------------
    _tp = ctx.get("trigger_payload") or {}
    lead_email_check = (ctx.get("email") or _tp.get("email") or "").strip().lower()
    if lead_email_check and lead_email_check in _load_suppression():
        if workflow["stage"] != "bounced-paused":
            _set_stage(conn, wf_id, "bounced-paused")
            _log({
                "event": "touch_suppressed",
                "wf_id": wf_id,
                "reason": "lead email in suppression list (bounced)",
                "lead_email": lead_email_check,
                "stage_set": "bounced-paused",
            })
            conn.commit()
        return 0

    # Stop if reply detected
    if _check_and_handle_reply(conn, workflow, ctx):
        conn.commit()
        return 0  # replied, not a new dispatch

    # Manual eyeball gate: founder leads can be queued now but must not auto-send
    # until Vlad has reviewed and flipped the approval flag in context_json.
    if ctx.get("manual_eyeball_required") and not ctx.get("vlad_approved_at"):
        _log({"event": "workflow_waiting_on_vlad", "wf_id": wf_id, "reason": "manual_eyeball_required"})
        return 0

    days = _days_since_start(seq)
    warmup_cap = _today_warmup_cap()
    warmup_remaining = max(0, warmup_cap - _count_email_dispatches_today(conn))

    if warmup_remaining == 0:
        _log({
            "event": "email_warmup_cap_reached",
            "wf_id": wf_id,
            "today_cap": warmup_cap,
        })

    # Find the FIRST touch that is due and not yet dispatched
    for touch in SEQUENCE:
        if days < touch["day"]:
            # Not due yet — stop scanning (SEQUENCE is sorted by day)
            break
        if _touch_done(seq, touch["kind"]):
            continue
        if touch["kind"] != "email-cold-1" and not _has_confirmed_sent_touch(conn, wf_id, "email-cold-1"):
            _log({
                "event": "touch_deferred",
                "wf_id": wf_id,
                "kind": touch["kind"],
                "reason": "awaiting_confirmed_sent_email_cold_1",
            })
            return 0
        if touch["channel"] == "email" and warmup_remaining <= 0:
            _log({
                "event": "touch_deferred",
                "wf_id": wf_id,
                "kind": touch["kind"],
                "reason": "warmup_cap_reached",
                "today_cap": warmup_cap,
            })
            return 0
        # This touch is due and not done — dispatch it
        # Ensure stage is 'sequence-active' once we start
        if workflow["stage"] == "cold-email-pending" and touch["kind"] == "email-cold-1":
            _set_stage(conn, wf_id, "sequence-active")

        dispatched = _dispatch_touch(conn, workflow, ctx, touch)
        if dispatched:
            if touch["channel"] == "email":
                warmup_remaining -= 1
                if warmup_remaining < 0:
                    raise RuntimeError(f"warmup budget overrun for {wf_id}: cap={warmup_cap}")
            conn.commit()
            return 1

    # Check if sequence is fully exhausted (breakup was logged)
    if _touch_done(seq, "email-breakup"):
        conn.execute(
            "UPDATE workflows SET stage='sequence-complete', updated_at=? WHERE id=?",
            (_now_iso(), wf_id),
        )
        conn.commit()

    return 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def tick(connection: sqlite3.Connection) -> int:
    """Scan all active qualified_lead workflows and dispatch any due touches.

    Returns the total count of touches dispatched this tick.
    Called from engine.py heartbeat() after sweep_stale_running_jobs().
    """
    try:
        rows = connection.execute(
            """
            SELECT * FROM workflows
             WHERE kind = 'qualified_lead'
               AND stage NOT IN ('replied','replied-sent','done','closed','unsubscribed',
                                  'disqualified','sequence-complete','bounced-paused')
               AND status NOT IN ('done','cancelled','failed')
             ORDER BY created_at ASC
            """,
        ).fetchall()
    except Exception as exc:
        _log({"event": "tick_query_error", "error": str(exc)})
        return 0

    total = 0
    total_limit = max(MAX_DISPATCHES_PER_TICK, _today_warmup_cap())
    for wf in rows:
        if total >= total_limit:
            break  # leave the rest for the next heartbeat tick
        try:
            total += _process_workflow(connection, wf)
        except Exception as exc:
            _log({"event": "workflow_error", "wf_id": wf["id"], "error": str(exc)[:500]})

    if total > 0:
        _log({"event": "tick_complete", "dispatched": total, "scanned": len(rows)})

    return total


# ---------------------------------------------------------------------------
# Smoke-test CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run sequencer tick against live DB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be dispatched")
    parser.add_argument("--workflow-ids", nargs="*", help="Limit to specific workflow IDs")
    args = parser.parse_args()

    from runtime.db import connect
    conn = connect()

    if args.dry_run:
        # Just show what's pending without dispatching
        rows = conn.execute(
            """
            SELECT id, kind, stage, status, context_json FROM workflows
             WHERE kind = 'qualified_lead'
               AND stage NOT IN ('replied','replied-sent','done','closed','unsubscribed',
                                  'disqualified','sequence-complete')
               AND status NOT IN ('done','cancelled','failed')
            """
        ).fetchall()
        for wf in rows:
            if args.workflow_ids and wf["id"] not in args.workflow_ids:
                continue
            ctx = json.loads(wf["context_json"] or "{}")
            seq = ctx.get("seq", {})
            days = _days_since_start(seq)
            done_kinds = {e["kind"] for e in seq.get("touch_log", [])}
            next_touches = [t for t in SEQUENCE if t["day"] <= days and t["kind"] not in done_kinds]
            print(
                f"{wf['id']} | stage={wf['stage']} | days={days} | "
                f"done={list(done_kinds) or 'none'} | "
                f"next={'|'.join(t['kind'] for t in next_touches[:3]) or 'none'}"
            )
    else:
        wf_filter = set(args.workflow_ids or [])
        if wf_filter:
            # Temporarily restrict: re-run tick but filtered
            all_rows = conn.execute(
                """
                SELECT * FROM workflows
                 WHERE kind = 'qualified_lead'
                   AND stage NOT IN ('replied','replied-sent','done','closed','unsubscribed',
                                      'disqualified','sequence-complete')
                   AND status NOT IN ('done','cancelled','failed')
                   AND id IN ({})
                """.format(",".join("?" * len(wf_filter))),
                list(wf_filter),
            ).fetchall()
            total = 0
            for wf in all_rows:
                try:
                    total += _process_workflow(conn, wf)
                except Exception as exc:
                    print(f"ERROR {wf['id']}: {exc}")
            print(f"Dispatched {total} touches for {len(all_rows)} workflows")
        else:
            n = tick(conn)
            print(f"Dispatched {n} touches")

    conn.close()
