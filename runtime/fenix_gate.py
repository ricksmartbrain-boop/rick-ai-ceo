"""Fenix preflight gate for outbound public artifacts.

Per `config/subagents.json`, Fenix is the compliance + brand reviewer:
  - Gates: customer naming, price changes, refund offers, legal responses,
    founder voice (anything saying "I" / "we" / "Vlad" claiming intent)
  - Triggers: outbound_preflight_public, blog_publish_request,
    legal_question_received, press_inquiry
  - Model: claude-opus-4-7 (smart judgment for edge cases)
  - CAN say NO (block). CANNOT auto-approve irreversible commits — escalates.

This module is the integration layer between the outbound_dispatcher and
the Fenix subagent. Two modes:

  RICK_FENIX_LIVE=0 (default — OBSERVE-ONLY):
    Runs the heuristic check, logs what WOULD have been blocked to
    ~/rick-vault/operations/fenix-observed.jsonl. Doesn't actually
    block any sends. Used for tuning the trigger heuristic before
    enforcement.

  RICK_FENIX_LIVE=1 (ENFORCE):
    Actually invokes Fenix's LLM review on flagged artifacts. If Fenix
    returns block/escalate, the send is suppressed and Vlad is notified
    via the deduped notify path. If Fenix returns approve, the send
    proceeds.

Channels considered "public" for Fenix purposes (others bypass the gate):
  moltbook, reddit, threads, instagram, linkedin, blog, x_twitter

Email is NOT in the public list — direct customer email goes through
Iris/Noa workflows that have their own approval gates.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OBSERVED_LOG = DATA_ROOT / "operations" / "fenix-observed.jsonl"
BLOCKED_LOG = DATA_ROOT / "operations" / "fenix-blocked.jsonl"
# Unified decisions log — every needs_review=True preflight outcome lands here
# regardless of mode/decision. Source of truth for accept-rate computation.
DECISIONS_LOG = DATA_ROOT / "operations" / "fenix-decisions.jsonl"

# Channels Fenix considers "public artifacts" (others bypass the gate)
PUBLIC_CHANNELS = {
    "moltbook", "reddit", "threads", "instagram",
    "linkedin", "blog", "x_twitter", "x", "twitter",
    "hackernews", "hn",
}

# 2026-04-24: email channels also gated. Separate set so future tuning
# can adjust triggers per category (e.g. transactional emails may
# legitimately mention prices; cold drips should not).
EMAIL_CHANNELS = {
    "email", "email_drip", "email_cold", "email_followup",
    "email_nurture", "email_blast",
}

GATED_CHANNELS = PUBLIC_CHANNELS | EMAIL_CHANNELS

# Heuristic triggers — if ANY match, the artifact gets Fenix review.
# Tuned conservatively: rather have a few false-positives than a missed legal/PII.
_TRIGGER_PATTERNS = [
    # Customer naming
    re.compile(r"\b(newton|mango|chris\s+laverdure|mykhailo|vlad(islav)?\s+belkins?)\b", re.I),
    # Price changes / explicit pricing claims
    re.compile(r"\$\s*\d{1,3}(,\d{3})*(\.\d+)?\s*(/\s*(mo|month|year|yr|annual|monthly))", re.I),
    # Refund / cancel offers
    re.compile(r"\b(refund(ed)?|chargeback|money[\s-]back)\b", re.I),
    # Legal language
    re.compile(r"\b(lawsuit|cease[\s-]and[\s-]desist|gdpr|ccpa|data[\s-]protection|copyright[\s-]violation|defamation)\b", re.I),
    # Founder-voice commitments
    re.compile(r"\b(i\s+(promise|guarantee|commit|pledge|will\s+personally))\b", re.I),
    re.compile(r"\b(we'll\s+(refund|reimburse|compensate))\b", re.I),
    # Sensitive operational claims
    re.compile(r"\b(\d{4,})\s+(customers?|users?|installs?|deployments?)\b", re.I),  # inflated numbers
    re.compile(r"\$\s*\d{1,3}(,\d{3})*\s*(MRR|ARR|revenue)\b", re.I),  # MRR/ARR claims
]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        # sort_keys matches the convention used elsewhere in rick-vault/operations.
        f.write(json.dumps({"ts": _now_iso(), **payload}, sort_keys=True) + "\n")


def _extract_text(payload: dict) -> str:
    """Pull all stringy fields from a payload — body, text, content, message,
    subject, title, etc. — into one searchable blob."""
    text_keys = ("body", "text", "content", "message", "subject", "title",
                 "post", "caption", "description", "html", "markdown")
    parts = []
    for k, v in (payload or {}).items():
        if k in text_keys and isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            parts.append(_extract_text(v))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(_extract_text(item))
    return "\n".join(parts)


def needs_fenix_review(channel: str, payload: dict) -> tuple[bool, list[str]]:
    """Decide whether this artifact needs Fenix's eye.

    Returns (needs_review, matched_pattern_summaries). Empty list when no
    triggers fire. Channels not in GATED_CHANNELS (PUBLIC ∪ EMAIL) bypass.
    """
    if (channel or "").lower() not in GATED_CHANNELS:
        return False, []
    text = _extract_text(payload)
    if not text.strip():
        return False, []
    matches: list[str] = []
    for pat in _TRIGGER_PATTERNS:
        m = pat.search(text)
        if m:
            matches.append(f"{pat.pattern[:40]}... → '{m.group(0)[:60]}'")
    return (len(matches) > 0), matches


def _request_fenix_llm_review(channel: str, payload: dict, matches: list[str]) -> dict:
    """Invoke Fenix subagent for actual review. Returns:
        {decision: 'approve'|'block'|'escalate', reason: str, model: str}
    Defensive: any error → 'escalate' (safer to over-escalate than auto-publish).
    """
    text = _extract_text(payload)
    prompt = (
        "You are Fenix, Rick's compliance & brand reviewer. A public artifact is about to "
        f"go live on {channel}. Heuristic flagged: {'; '.join(matches[:5])}.\n\n"
        f"ARTIFACT TEXT:\n---\n{text[:3000]}\n---\n\n"
        "Decide: APPROVE (safe to publish), BLOCK (don't publish, with reason), or "
        "ESCALATE (Vlad must look). Output JSON only:\n"
        "  {\"decision\": \"approve|block|escalate\", \"reason\": \"...\"}\n"
        "Default to ESCALATE for: customer names + numbers, refund/legal language, "
        "founder-voice commitments, MRR/ARR claims, anything irreversible. "
        "APPROVE only if the artifact is clearly safe + on-brand."
    )
    fallback = json.dumps({"decision": "escalate", "reason": "fenix LLM unreachable, defaulting to human review"})
    try:
        from runtime.llm import generate_text
        result = generate_text("review", prompt, fallback)  # review route → opus-4-7
        text_out = (result.content if hasattr(result, "content") else str(result)).strip()
        # Strip code-fence markers if model wrapped in ```json
        text_out = re.sub(r"^```(?:json)?\s*", "", text_out)
        text_out = re.sub(r"\s*```$", "", text_out)
        decision_obj = json.loads(text_out)
        decision = (decision_obj.get("decision") or "escalate").lower().strip()
        if decision not in ("approve", "block", "escalate"):
            decision = "escalate"
        return {
            "decision": decision,
            "reason": (decision_obj.get("reason") or "")[:500],
            "model": getattr(result, "model_used", "unknown"),
        }
    except Exception as exc:
        return {"decision": "escalate", "reason": f"fenix exception: {str(exc)[:200]}", "model": "fallback"}


def preflight(conn: sqlite3.Connection, channel: str, payload: dict, job_id: str | None = None) -> dict:
    """Run Fenix preflight on an outbound artifact.

    Returns a result dict with: action ('proceed'|'block'|'escalate'),
    reason (str), matched_triggers (list), live (bool), model (str|None).

    In OBSERVE mode (RICK_FENIX_LIVE!=1): always returns action='proceed'
    after logging triggers. The caller continues sending.

    In LIVE mode (RICK_FENIX_LIVE=1): when triggers fire, calls Fenix LLM.
    'block' or 'escalate' → action != 'proceed' so caller suppresses the send.
    """
    needs_review, matches = needs_fenix_review(channel, payload)
    base = {
        "channel": channel,
        "job_id": job_id,
        "needs_review": needs_review,
        "matched_triggers": matches,
        "live": False,
        "action": "proceed",
        "reason": "no triggers" if not needs_review else "observe-mode default",
        "model": None,
    }

    if not needs_review:
        return base

    live = os.getenv("RICK_FENIX_LIVE", "0").strip().lower() in ("1", "true", "yes")
    base["live"] = live

    if not live:
        _log(OBSERVED_LOG, {**base, "would_have_invoked": True})
        # Trimmed payload for decisions log — keeps lines well under 4KB so
        # POSIX O_APPEND atomicity holds under concurrent writes.
        _log(DECISIONS_LOG, {
            "channel": base["channel"], "job_id": base["job_id"],
            "action": base["action"], "mode": "observe",
            "trigger_count": len(base["matched_triggers"]),
        })
        return base

    # LIVE mode — invoke Fenix
    decision = _request_fenix_llm_review(channel, payload, matches)
    action_map = {"approve": "proceed", "block": "block", "escalate": "escalate"}
    base["action"] = action_map.get(decision["decision"], "escalate")
    base["reason"] = decision["reason"]
    base["model"] = decision["model"]

    _log(DECISIONS_LOG, {
        "channel": base["channel"], "job_id": base["job_id"],
        "action": base["action"], "mode": "live",
        "model": base["model"],
        "reason": (base["reason"] or "")[:200],
        "trigger_count": len(base["matched_triggers"]),
    })

    if base["action"] != "proceed":
        _log(BLOCKED_LOG, base)
        # Notify Vlad via deduped helper (won't spam — same kind+payload → dedupes)
        try:
            from runtime.engine import notify_operator_deduped
            text_preview = _extract_text(payload)[:200]
            notify_operator_deduped(
                conn,
                f"🛡️ Fenix {base['action'].upper()}: {channel} job {job_id} — {decision['reason'][:120]}\n"
                f"Preview: {text_preview}",
                kind=f"fenix_{base['action']}",
                dedup_window_hours=2,
                purpose="ops",
            )
        except Exception:
            pass
    return base
