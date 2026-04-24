#!/usr/bin/env python3
"""TIER-3.5 #A4 — Draft a counter-pitch reply when classifier returns objection_with_counter.

CLI:
  python3 draft-counter.py --thread-id <id> --objection-text "..." [--prospect-id <id>] [--dry-run]

Writes draft JSON to ~/rick-vault/mailbox/drafts/counter-pitch/<thread_id>-<ts>.json
NEVER auto-sends. Vlad reviews via Telegram /inbox (TIER-3.5 #A12, separate ship).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2].parent
sys.path.insert(0, str(ROOT))

from runtime.db import connect  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DRAFTS_DIR = DATA_ROOT / "mailbox" / "drafts" / "counter-pitch"
LOG_FILE = DATA_ROOT / "operations" / "counter-pitch.jsonl"
CASE_STUDY_DIR = Path(os.getenv("RICK_SITE_DIR", str(Path.home() / "meetrick-site"))) / "case-studies"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(payload: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload["ts"] = _now_iso()
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def _classify_objection(objection_text: str) -> str:
    """Cheap keyword-based classification of objection theme."""
    t = (objection_text or "").lower()
    if any(k in t for k in ("price", "pricing", "cost", "expensive", "afford", "budget", "$")):
        return "pricing"
    if any(k in t for k in ("not now", "later", "next quarter", "timing", "after we ship", "after launch")):
        return "timing"
    if any(k in t for k in ("not a fit", "wrong stage", "different stage", "we're enterprise", "we're solo")):
        return "fit"
    if any(k in t for k in ("trust", "skeptical", "proof", "case study", "examples", "track record", "credible")):
        return "trust"
    return "other"


def _scan_case_studies() -> list[dict]:
    """Return [{slug, title, snippet}] for available case studies. Tolerates empty dir."""
    if not CASE_STUDY_DIR.is_dir():
        return []
    out: list[dict] = []
    for path in CASE_STUDY_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".md", ".mdx", ".html"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Title from first H1 or first non-blank line
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE) or re.search(r"<h1[^>]*>(.+?)</h1>", text)
        title = (title_match.group(1).strip() if title_match else path.stem)[:120]
        snippet = re.sub(r"<[^>]+>|^#.*$|^---.*?---\n", "", text, flags=re.MULTILINE | re.DOTALL)
        snippet = " ".join(snippet.split())[:300]
        out.append({"slug": path.stem, "title": title, "snippet": snippet})
    return out


def _pick_case_study(case_studies: list[dict], objection_class: str) -> dict | None:
    """Match a case study to the objection theme. Loose keyword overlap."""
    if not case_studies:
        return None
    keywords_by_class = {
        "pricing": ("price", "$", "saved", "value"),
        "timing": ("first week", "30 days", "launch", "ship"),
        "fit": ("solo", "founder", "small team"),
        "trust": ("real", "result", "verified", "metric"),
    }
    keywords = keywords_by_class.get(objection_class, ())
    for cs in case_studies:
        text = (cs.get("title", "") + " " + cs.get("snippet", "")).lower()
        if any(k in text for k in keywords):
            return cs
    # Fallback: just return first available
    return case_studies[0] if case_studies else None


def _load_thread_context(con: sqlite3.Connection, thread_id: str) -> dict:
    """Pull thread metadata + last 3 inbound texts for context."""
    if not thread_id:
        return {}
    try:
        thread = con.execute(
            "SELECT thread_id, subject, prospect_id, status, last_inbound_at "
            "FROM email_threads WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        ).fetchone()
        return {"subject": (thread["subject"] if thread else "") or "(no subject)",
                "thread_status": thread["status"] if thread else None}
    except sqlite3.OperationalError:
        return {}


def _draft(thread_id: str, objection_text: str, prospect_id: str | None) -> dict:
    objection_class = _classify_objection(objection_text)
    case_studies = _scan_case_studies()
    case = _pick_case_study(case_studies, objection_class)

    con = connect()
    try:
        ctx = _load_thread_context(con, thread_id)
        # 2026-04-24: pattern READ side fanned out to counter_pitch.
        # Surfaces top-3 most-effective patterns for this skill so the
        # objection-handler benefits from accumulated wins. Shielded:
        # any failure → empty list, draft still ships.
        picked_patterns: list[dict] = []
        pattern_context = ""
        try:
            from runtime.patterns import pick_patterns, format_pattern_context
            picked_patterns = pick_patterns(con, "counter_pitch", top_n=3)
            pattern_context = format_pattern_context(picked_patterns)
        except Exception:
            picked_patterns = []
            pattern_context = ""
    finally:
        con.close()

    subject_orig = ctx.get("subject", "(no subject)")
    subject = subject_orig if subject_orig.lower().startswith("re:") else f"Re: {subject_orig}"

    case_str = ""
    if case:
        case_str = (
            f"\n\nReference case study available (cite if relevant): "
            f"'{case['title']}' — {case['snippet'][:200]}"
        )

    prompt = (
        "You are Rick — an autonomous AI CEO at meetrick.ai. A prospect just "
        f"raised an objection (theme: '{objection_class}'). They're still engaged "
        "(not a hard 'no'). Draft a SHORT (5-7 sentences max) counter-pitch reply.\n\n"
        "Voice: founder-direct, dry humor, opinion-first, no buzzwords, no 'I hope this finds you well'.\n\n"
        "Rules:\n"
        "- Address the SPECIFIC objection, not generic.\n"
        "- If a relevant case study is provided, cite it (with attribution).\n"
        "- Soft re-pitch from a new angle — not 'but you should buy because…'\n"
        "- End with ONE concrete next step (15-min call OR a specific question).\n"
        "- Real MRR is $9 / 1 paying customer (Newton). Don't claim metrics we don't have.\n"
        "- Em dashes OK.\n\n"
        f"PROSPECT'S OBJECTION:\n{objection_text}\n"
        f"{case_str}"
        f"{pattern_context}\n"
        "Output: just the email body. No greeting line (I'll add 'Hi <name>,' separately).\n"
    )
    fallback = (
        "Fair concern — let me push back gently. The thing most people miss "
        "is that the cost of NOT having someone running outbound 24/7 is "
        "usually higher than the experiment ticket. I'd rather show you than "
        "argue it: 15 min this week, I'll walk you through what one autonomous "
        "Rick has done in the last 7 days. Worth your time?\n\n— Rick"
    )

    try:
        from runtime.llm import generate_text  # noqa: WPS433
        result = generate_text("writing", prompt, fallback)
        body = (result.content if hasattr(result, "content") else str(result)).strip()[:1500]
        meta_extra = {"model": getattr(result, "model_used", "claude-sonnet-4-6"), "fallback_used": False}
    except Exception as e:  # noqa: BLE001
        body = fallback
        meta_extra = {"model": "fallback", "fallback_used": True, "error": str(e)[:200]}

    # 2026-04-24: pattern CREDIT side. Heuristic for "won": Rick produced
    # non-fallback content with reasonable length (≥100 chars) AND addressed
    # the objection class (presence of objection-related word in body).
    # Replace with reply-rate signal once Phase G inbound matures.
    if picked_patterns:
        try:
            from runtime.patterns import record_pattern_outcome
            won = (
                not meta_extra.get("fallback_used", False)
                and len(body or "") >= 100
                and (objection_class.lower() in body.lower() or len(body or "") >= 300)
            )
            con2 = connect()
            try:
                record_pattern_outcome(
                    con2,
                    pattern_ids=[p["id"] for p in picked_patterns if p.get("id")],
                    success=won,
                )
            finally:
                con2.close()
        except Exception:
            pass

    return {
        "draft_id": f"cp_{uuid.uuid4().hex[:10]}",
        "thread_id": thread_id,
        "prospect_id": prospect_id,
        "subject": subject,
        "body": body,
        "objection_class": objection_class,
        "case_study_cited": case["slug"] if case else None,
        "patterns_used": [p.get("id") for p in picked_patterns],
        "created_at": _now_iso(),
        **meta_extra,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread-id", required=True)
    ap.add_argument("--objection-text", required=True)
    ap.add_argument("--prospect-id", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        draft = _draft(args.thread_id, args.objection_text, args.prospect_id)
    except Exception as e:  # noqa: BLE001
        _log({"error": str(e)[:200], "thread_id": args.thread_id})
        print(json.dumps({"status": "error", "error": str(e)[:200]}))
        return 1

    if args.dry_run:
        print(json.dumps({"status": "dry-run", "draft": draft}, indent=2))
        return 0

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_thread = re.sub(r"[^a-zA-Z0-9._-]", "_", (args.thread_id or "unknown"))[:40]
    path = DRAFTS_DIR / f"{safe_thread}-{draft['draft_id']}.json"
    path.write_text(json.dumps(draft, indent=2), encoding="utf-8")
    _log({"action": "draft-written", "path": str(path), "thread_id": args.thread_id,
          "objection_class": draft["objection_class"]})
    print(json.dumps({"status": "ok", "path": str(path), "draft_id": draft["draft_id"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
