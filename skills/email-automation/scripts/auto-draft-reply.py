#!/usr/bin/env python3
"""Auto-draft reply generator — Phase G additive layer (observe-mode only).

Given a classified inbound triage row, pulls full thread context, generates
an opus- or sonnet-quality draft reply, and saves it to:
  ~/rick-vault/mailbox/drafts/auto/<thread-safe-id>-<label>-<ts>.json

NEVER auto-sends. Drafts require Vlad review. auto_send=false always.

Smart-models invariant (PERMANENT):
  HIGH_INTENT (sales_inquiry, scheduling_request, pricing_question)
    → route "review" → claude-opus-4-7
  WARM (objection_with_counter, question, referral_request, support_request)
    → route "writing" → claude-sonnet-4-6

CLI:
  # Dry-run with a row JSON (router calls this):
  python3 auto-draft-reply.py --row-json '{...}' --label sales_inquiry --dry-run
  # Live:
  python3 auto-draft-reply.py --row-json '{...}' --label pricing_question --live
  # Manual fields:
  python3 auto-draft-reply.py --label scheduling_request --from-email x@y.com \
      --from-name "Jamie" --subject "Re: AI CEO" --body "..." --live
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Resolve REPO_ROOT: skills/email-automation/scripts → ../../.. → workspace root
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.llm import generate_text  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
ENV_FILE = Path(os.getenv("RICK_ENV_FILE", str(Path.home() / "clawd" / "config" / "rick.env")))
TRIAGE_DIR = DATA_ROOT / "mailbox" / "triage"
AUTO_DRAFTS_DIR = DATA_ROOT / "mailbox" / "drafts" / "auto"
LOG_FILE = DATA_ROOT / "operations" / "auto-draft-reply.jsonl"

# Smart-models invariant — do NOT downgrade these routes
HIGH_INTENT_LABELS: frozenset[str] = frozenset({"sales_inquiry", "scheduling_request", "pricing_question"})
WARM_LABELS: frozenset[str] = frozenset({"objection_with_counter", "question", "referral_request", "support_request"})
ALL_DRAFTABLE_LABELS: frozenset[str] = HIGH_INTENT_LABELS | WARM_LABELS

# Per-label drafting guidance injected into the prompt
_LABEL_GUIDANCE: dict[str, str] = {
    "sales_inquiry": (
        "They are curious and evaluating. Goal: book a 15-min call or get them to "
        "meetrick.ai/deploy. Offer one specific slot ('Tuesday or Wednesday 3pm UK / 10am ET '). "
        "Lead with their specific pain, not a feature list. Keep it warm and under 5 sentences."
    ),
    "scheduling_request": (
        "They explicitly want to book a call or meeting. Confirm enthusiastically, propose "
        "2-3 concrete time options in the next 72 hours. Include a Calendly placeholder: "
        "[calendly-link]. Match their energy level — if they seem excited, be excited back."
    ),
    "pricing_question": (
        "They asked about cost. Lead with value, then give a crisp number. "
        "Rick Pro = $9/mo. Managed AI CEO = $499/mo. Deploy tier = $2,500-$10K/mo + $5K setup. "
        "Offer a quick 15-min call to match them to the right tier. Don't over-explain."
    ),
    "objection_with_counter": (
        "They raised an objection but stayed engaged. Acknowledge it genuinely — don't dismiss. "
        "Flip exactly ONE point with a concrete, specific example (real metric or customer outcome). "
        "Never defend the product generically. End with a soft forward pull: "
        "a question, a trial offer, or a call invite. Max 4 sentences."
    ),
    "question": (
        "They asked a genuine question with no clear buy signal yet. Answer directly and briefly. "
        "Leave one door open at the end: 'Happy to walk through it live if that would be faster.' "
        "Do not oversell. Curiosity from you is more powerful than a pitch right now."
    ),
    "referral_request": (
        "They want an intro or recommendation. Be genuinely helpful: think of 1-2 concrete names "
        "or resources you can offer. Keep it short and warm. If you don't have someone, "
        "say so honestly and pivot to what you can do for them directly."
    ),
    "support_request": (
        "An existing customer needs help. Lead with empathy: 'Got it — let me fix this.' "
        "Give a direct answer if you have one. If not, tell them what you'll do and by when. "
        "Never make a customer feel like they're a ticket number."
    ),
}


def _load_env() -> None:
    if not ENV_FILE.exists():
        return
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(event: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ran_at": _now_iso(), **event}) + "\n")
    except OSError:
        pass


def _safe_filename(s: str, maxlen: int = 48) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", s)[:maxlen]


# ── Thread context retrieval ─────────────────────────────────────────────────

def _find_thread_context(thread_id: str, current_msg_id: str) -> list[dict]:
    """Scan last 14 days of triage files for prior messages in the same thread.

    Returns rows ordered oldest-first (original outbound / earlier exchange first).
    """
    if not thread_id:
        return []
    matches: list[dict] = []
    try:
        files = sorted(TRIAGE_DIR.glob("inbound-*.jsonl"))[-14:]
        for f in files:
            try:
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg_id = row.get("message_id", "")
                    if msg_id and msg_id == current_msg_id:
                        continue  # skip the current message itself
                    row_thread = row.get("thread_id", "")
                    row_msg = row.get("message_id", "")
                    if row_thread == thread_id or row_msg == thread_id:
                        matches.append(row)
            except OSError:
                continue
    except Exception:
        pass
    # Deduplicate by message_id, keep oldest first
    seen: set[str] = set()
    unique: list[dict] = []
    for row in matches:
        mid = row.get("message_id", "")
        if mid not in seen:
            seen.add(mid)
            unique.append(row)
    return unique  # already in file-date order (oldest → newest)


def _lookup_prospect(from_email: str) -> dict:
    """Pull prospect_pipeline row for context enrichment. Non-fatal on failure."""
    try:
        from runtime.db import connect as db_connect
        conn = db_connect()
        try:
            row = conn.execute(
                "SELECT * FROM prospect_pipeline WHERE username = ? LIMIT 1",
                (from_email.strip().lower(),),
            ).fetchone()
            if row:
                return dict(row)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        pass
    return {}


# ── Prompt construction ──────────────────────────────────────────────────────

def _build_prompt(row: dict, label: str, thread_ctx: list[dict], prospect: dict) -> str:
    from_email = row.get("from", "unknown@unknown.com")
    from_name = (
        row.get("from_name")
        or prospect.get("full_name")
        or from_email.split("@")[0].replace(".", " ").title()
    )
    first_name = from_name.split()[0] if from_name else "there"
    subject = (row.get("subject") or "(no subject)")[:200]
    body = (row.get("body") or "")[:3000]

    # Person context from signature or prospect record
    sig = row.get("signature") or {}
    title = sig.get("title") or prospect.get("job_title") or prospect.get("title") or ""
    company = sig.get("company") or prospect.get("company") or ""

    person_line = from_name
    if title and company:
        person_line = f"{from_name} ({title} at {company})"
    elif company:
        person_line = f"{from_name} at {company}"
    elif title:
        person_line = f"{from_name}, {title}"

    # Prior thread messages (oldest first = original outbound context)
    thread_section = ""
    if thread_ctx:
        # Take the oldest message as original context
        original = thread_ctx[0]
        orig_body = (original.get("body") or "")[:800]
        orig_subj = original.get("subject") or ""
        thread_section = (
            "\n\nTHREAD CONTEXT (prior message):\n"
            f"Subject: {orig_subj}\n"
            f"{orig_body}"
        )
        # If there are more messages, summarise them briefly
        if len(thread_ctx) > 1:
            thread_section += f"\n\n[+ {len(thread_ctx) - 1} earlier message(s) in thread]"

    guidance = _LABEL_GUIDANCE.get(label, "Write a helpful, personalized reply.")

    # Inject full comm history so the draft never repeats prior outreach
    _prior_comms_block = ""
    try:
        from runtime.comm_history import get_history as _ch_get, render_for_prompt as _ch_render
        _ch_hist = _ch_get(from_email, days_back=90)
        if _ch_hist:
            _prior_comms_block = _ch_render(_ch_hist, max_chars=2000) + "\n\n"
    except Exception:
        _prior_comms_block = ""

    prompt = (
        _prior_comms_block +
        "You are Rick — autonomous AI CEO of meetrick.ai, building toward $100K MRR.\n"
        "You received an inbound email reply classified as: {label}\n\n"
        "FROM: {person_line} <{from_email}>\n"
        "SUBJECT: {subject}\n"
        "{thread_section}\n\n"
        "THEIR MESSAGE:\n"
        "---\n"
        "{body}\n"
        "---\n\n"
        "DRAFT GUIDANCE: {guidance}\n\n"
        "VOICE RULES:\n"
        "- Sharp, warm, genuine. Not corporate, not stiff, not robotic.\n"
        "- Use their first name: {first_name}\n"
        "- 3-5 sentences max unless they asked something complex.\n"
        "- End with exactly ONE clear next step or question, not a list.\n"
        "- No em dashes. Use plain punctuation only.\n"
        "- No filler phrases: 'absolutely', 'certainly', 'of course', 'great question'.\n"
        "- Self-aware warmth: Rick is an AI CEO, not pretending to be human. Lean into it.\n"
        "- Sign off as: Rick (AI CEO, meetrick.ai)\n\n"
        "This is a DRAFT for Vlad's review before sending. Write only the email body.\n"
        "No Subject line. No meta-commentary. Start directly with the reply.\n"
    ).format(
        label=label,
        person_line=person_line,
        from_email=from_email,
        subject=subject,
        thread_section=thread_section,
        body=body,
        guidance=guidance,
        first_name=first_name,
    )
    return prompt


# ── Core generation ──────────────────────────────────────────────────────────

def generate_draft(row: dict, label: str, dry_run: bool) -> dict:
    """Generate the auto-draft and save it to AUTO_DRAFTS_DIR.

    Returns a status dict that reply_router stamps onto the triage row.
    """
    from_email = (row.get("from") or "").strip()
    if not from_email:
        return {"action": "skip-no-from"}
    if label not in ALL_DRAFTABLE_LABELS:
        return {"action": "skip-not-draftable", "label": label}

    thread_id = row.get("thread_id") or row.get("message_id") or ""
    current_msg_id = row.get("message_id") or ""

    thread_ctx = _find_thread_context(thread_id, current_msg_id)
    prospect = _lookup_prospect(from_email)
    prompt = _build_prompt(row, label, thread_ctx, prospect)

    # Smart-models invariant: NEVER downgrade.
    # Always use route "writing" for the correct email-persona system prompt.
    # For HIGH_INTENT, temporarily force the writing-route model to opus-4-7
    # via env override so we get opus intelligence without the red-team reviewer
    # system prompt that lives on the "review" route.
    route = "writing"
    is_high_intent = label in HIGH_INTENT_LABELS
    model_hint = "claude-opus-4-7" if is_high_intent else "claude-sonnet-4-6"

    if dry_run:
        return {
            "action": "would-draft",
            "label": label,
            "route": route,
            "model_hint": model_hint,
            "prompt_chars": len(prompt),
            "thread_ctx_msgs": len(thread_ctx),
            "from": from_email,
            "thread_id": thread_id,
        }

    # ── LLM call ────────────────────────────────────────────────────────────
    # For HIGH_INTENT: temporarily override the writing-route model env var to
    # claude-opus-4-7. We restore it in the finally block — thread-safe for
    # this subprocess-isolated call (the router spawns us as a child process).
    _env_key = "RICK_MODEL_ANTHROPIC_WORKHORSE"
    _old_val = os.environ.get(_env_key)
    try:
        if is_high_intent:
            os.environ[_env_key] = "claude-opus-4-7"
        result = generate_text(route, prompt, fallback="")
        draft_text = (result.content if hasattr(result, "content") else str(result) or "").strip()
    except Exception as exc:
        _log({"action": "draft-error", "label": label, "from": from_email, "error": str(exc)[:300]})
        return {"action": "draft-error", "error": str(exc)[:300]}
    finally:
        if is_high_intent:
            if _old_val is None:
                os.environ.pop(_env_key, None)
            else:
                os.environ[_env_key] = _old_val

    if not draft_text:
        _log({"action": "draft-empty", "label": label, "from": from_email})
        return {"action": "draft-empty", "model": getattr(result, "model", "?")}

    # ── Save draft ───────────────────────────────────────────────────────────
    AUTO_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    key = thread_id or from_email
    fname = f"{_safe_filename(key, 40)}-{label}-{ts}.json"
    path = AUTO_DRAFTS_DIR / fname

    sig = row.get("signature") or {}
    draft_payload = {
        "from_email": from_email,
        "from_name": row.get("from_name") or "",
        "from_title": sig.get("title") or prospect.get("title") or "",
        "from_company": sig.get("company") or prospect.get("company") or "",
        "subject": f"Re: {(row.get('subject') or '').lstrip('Re: ').lstrip('RE: ')}",
        "thread_id": thread_id,
        "label": label,
        "route": route,
        "model": getattr(result, "model", None) or model_hint,
        "body": draft_text,
        "original_reply_body": (row.get("body") or "")[:1000],
        "thread_ctx_msgs": len(thread_ctx),
        "review_required": True,
        "auto_send": False,          # observe-mode: NEVER auto-send
        "created_at": _now_iso(),
    }

    try:
        path.write_text(json.dumps(draft_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        _log({"action": "save-error", "label": label, "from": from_email, "error": str(exc)[:200]})
        return {"action": "save-error", "error": str(exc)[:200]}

    out = {
        "action": "auto-drafted",
        "label": label,
        "route": route,
        "model": draft_payload["model"],
        "path": str(path),
        "from": from_email,
        "body_chars": len(draft_text),
        "thread_ctx_msgs": len(thread_ctx),
    }
    _log(out)
    return out


# ── CLI entry point ──────────────────────────────────────────────────────────

def main() -> int:
    _load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--row-json", default="",
                    help="Full triage row as a JSON string (preferred; router passes this)")
    ap.add_argument("--thread-id", default="")
    ap.add_argument("--label", required=True, choices=sorted(ALL_DRAFTABLE_LABELS))
    ap.add_argument("--from-email", default="")
    ap.add_argument("--from-name", default="")
    ap.add_argument("--subject", default="")
    ap.add_argument("--body", default="")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    args = ap.parse_args()

    if args.row_json:
        try:
            row = json.loads(args.row_json)
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": f"invalid-row-json: {exc}"}))
            return 1
    else:
        row = {
            "thread_id": args.thread_id,
            "message_id": args.thread_id,
            "from": args.from_email,
            "from_name": args.from_name,
            "subject": args.subject,
            "body": args.body,
        }

    # Live gate — require env var like other Phase G scripts
    dry_run = args.dry_run
    if not dry_run and os.getenv("RICK_REPLY_ROUTER_LIVE") != "1":
        dry_run = True

    result = generate_draft(row, args.label, dry_run)
    print(json.dumps(result))
    return 0 if result.get("action") not in ("draft-error", "save-error", "skip-not-draftable") else 1


if __name__ == "__main__":
    sys.exit(main())
