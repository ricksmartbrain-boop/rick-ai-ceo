#!/usr/bin/env python3
"""Reply-watcher — 10-minute fast-lane for real inbound human replies.

RE-KEYED 2026-07-13 (inbound-capture repair): the old version watched
workflows WHERE kind='qualified_lead' AND stage='sequence-active' — a set
nothing has created since the May pruning. Result: 5,639 consecutive no-op
runs since 2026-05-22 while real replies (Adam/Neurvance price ask,
SyncBank consult) went unwatched. It now keys on the REAL inbound sources:
the imap-watcher triage JSONL (classified by reply-classifier) and the
email_threads table.

Also removed here: the direct Gmail IMAP check (destructive RFC822 fetch
set \\Seen and raced imap-watcher — single-consumer inbox rule: ONLY
runtime.inbound.imap_watcher touches IMAP; everything downstream reads
triage) and the Resend open-tracker (it keyed on the same dead workflow set).

On first detection of a new classified human reply:
  1. Generates a draft response via claude-opus-4-8 (NO auto-send)
  2. Saves draft  → ~/rick-vault/mailbox/drafts/auto/{triage_id}.json
  3. Fires notify_operator_deduped with PURPOSE=revenue (bypasses dedup)
  4. Marks the email_threads row status='replied' + intent_class,
     and inserts a follow_up_queue row (routing into follow-up)
  5. Writes reply-watcher.jsonl event row
  6. Updates digest: "Reply watch: {N} replies (watching {M} rows)"

State file: ~/rick-vault/control/reply-watcher-state.json
  {
    "handled": { "<triage_row_id>": "<iso_ts>" },   # already escalated
    "open_signals": {},                              # legacy, unused
    "last_run": "<iso_ts>"
  }

Usage:
    python3 scripts/reply-watcher.py              # normal run
    python3 scripts/reply-watcher.py --dry-run    # no DB writes / no alerts
    python3 scripts/reply-watcher.py --verbose    # extra output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DATA_ROOT   = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DB_PATH     = Path(os.getenv("RICK_RUNTIME_DB_FILE", str(DATA_ROOT / "runtime" / "rick-runtime.db")))
STATE_FILE  = DATA_ROOT / "control" / "reply-watcher-state.json"
LOG_FILE    = DATA_ROOT / "operations" / "reply-watcher.jsonl"
DRAFT_DIR   = DATA_ROOT / "mailbox" / "drafts" / "auto"
TRIAGE_DIR  = DATA_ROOT / "mailbox" / "triage"

# Models — smart-models invariant: only full models for draft generation
DRAFT_MODEL_ANTHROPIC = "claude-opus-4-8"
DRAFT_MODEL_OPENAI    = "gpt-4o"     # fallback if Anthropic unavailable

# Classifier labels that mean "a human wrote to us and it's warm enough to
# fast-lane". unsubscribe / not_interested / objection / automated_notification
# are handled by the router alone (suppression, closed-lost, noise-archive).
WATCH_LABELS = {
    "sales_inquiry", "pricing_question", "scheduling_request", "question",
    "objection_with_counter", "referral_request", "support_request",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _log_event(row: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"handled": {}, "open_signals": {}, "last_run": ""}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Load classified human replies from the triage pipe (the real source)
# ---------------------------------------------------------------------------

def _load_watch_rows(handled: dict[str, str], verbose: bool = False) -> list[dict]:
    """Return unhandled classified triage rows with a WATCH_LABELS label.

    Reads today's + yesterday's inbound-*.jsonl (imap-watcher output, labels
    added in-place by reply-classifier). Rows without a classification yet are
    left for a later run — the classifier fills them within its own 10-min loop.
    """
    rows: list[dict] = []
    if not TRIAGE_DIR.exists():
        return rows
    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    seen_ids: set[str] = set()
    for day in (today, yesterday):
        tpath = TRIAGE_DIR / f"inbound-{day}.jsonl"
        if not tpath.exists():
            continue
        for line in tpath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            rid   = r.get("id") or ""
            label = r.get("classification") or ""
            em    = (r.get("from") or "").lower().strip()
            if not rid or rid in seen_ids or rid in handled:
                continue
            if label not in WATCH_LABELS or not em:
                continue
            # Defense in depth — imap-watcher already skips self-sends
            if em.endswith("@meetrick.ai") or em == "vladislav@belkins.io":
                continue
            seen_ids.add(rid)
            rows.append(r)
    if verbose and rows:
        print(f"  [triage] {len(rows)} new classified human repl(ies)")
    return rows


def _row_ctx(row: dict) -> dict:
    """Build the drafting/alert context from a triage row."""
    em = (row.get("from") or "").lower().strip()
    return {
        "triage_id":   row.get("id") or "",
        "email":       em,
        "name":        row.get("from_name") or "",
        "company":     em.rpartition("@")[2],
        "opener_subj": row.get("subject") or "",
        "opener_body": "",
        "label":       row.get("classification") or "",
        "thread_id":   row.get("thread_id") or "",
        "received_at": row.get("received_at") or row.get("ingested_at") or "",
    }


# ---------------------------------------------------------------------------
# 2. Auto-drafter — opus-4-8 / gpt fallback only (smart-models invariant)
# ---------------------------------------------------------------------------

def _generate_draft(ctx: dict, reply_body: str, reply_label: str, dry_run: bool, verbose: bool) -> dict | None:
    """Generate a reply draft using claude-opus-4-8. NO auto-send.

    Returns draft dict or None on failure.
    """
    if dry_run:
        return {
            "draft_body": f"[DRY-RUN DRAFT]\nRe: {ctx['opener_subj']}\n\nThanks for replying, {ctx['name']}. [Generated response would appear here]",
            "model":      DRAFT_MODEL_ANTHROPIC,
            "dry_run":    True,
        }

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    openai_key    = os.getenv("OPENAI_API_KEY", "").strip()

    prompt = f"""You are Rick, AI CEO of meetrick.ai — sharp, warm, commercially serious.
Draft a reply to this inbound email.

SENDER: {ctx['name']} ({ctx['email']})
THEIR SUBJECT: {ctx['opener_subj']}

THEIR MESSAGE:
{reply_body[:1500]}

REPLY INTENT LABEL: {reply_label}

Draft a reply that:
- Acknowledges their specific message naturally (no "Great question!" opener)
- Moves toward a concrete next step (call booking, demo, or clarifying question)
- Is 3-5 sentences max — punchy, not corporate
- Signs off as Rick, AI CEO, meetrick.ai
- NO auto-send: this is for Vlad's review. Write the email body only, no subject line.
- NEVER commit to payments, prices, discounts, or contract terms — those are owner-only.

If label is 'sales_inquiry': move toward a 15-min call.
If label is 'question': answer directly + CTA."""

    # Try Anthropic first
    if anthropic_key:
        try:
            import urllib.request
            payload = json.dumps({
                "model":      DRAFT_MODEL_ANTHROPIC,
                "max_tokens": 500,
                "messages":   [{"role": "user", "content": prompt}],
            }).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "x-api-key":         anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type":      "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            body = result["content"][0]["text"].strip()
            return {"draft_body": body, "model": DRAFT_MODEL_ANTHROPIC, "prompt_tokens": result.get("usage", {}).get("input_tokens", 0)}
        except Exception as exc:
            if verbose:
                print(f"  [drafter] Anthropic error: {exc}")

    # Fallback: OpenAI
    if openai_key:
        try:
            import urllib.request
            payload = json.dumps({
                "model":    DRAFT_MODEL_OPENAI,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
            }).encode()
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type":  "application/json",
                    "User-Agent": "meetrick-rick/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            body = result["choices"][0]["message"]["content"].strip()
            return {"draft_body": body, "model": DRAFT_MODEL_OPENAI}
        except Exception as exc:
            if verbose:
                print(f"  [drafter] OpenAI error: {exc}")

    return None


def _save_draft(ctx: dict, draft: dict, row: dict) -> str:
    """Save draft to ~/rick-vault/mailbox/drafts/auto/{triage_id}.json. Returns path."""
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    path = DRAFT_DIR / f"{ctx['triage_id']}.json"
    payload = {
        "triage_id":      ctx["triage_id"],
        "prospect_email": ctx["email"],
        "prospect_name":  ctx["name"],
        "company":        ctx["company"],
        "reply_body":     (row.get("body") or "")[:2000],
        "reply_label":    ctx["label"],
        "reply_ts":       ctx["received_at"],
        "reply_source":   "triage",
        "thread_id":      ctx["thread_id"],
        "draft_body":     draft.get("draft_body", ""),
        "draft_subject":  f"Re: {ctx['opener_subj']}",
        "model":          draft.get("model", ""),
        "review_required": True,
        "auto_send":      False,
        "created_at":     _now_iso(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 3. Thread + follow-up routing (email_threads / follow_up_queue)
# ---------------------------------------------------------------------------

def _mark_thread_replied(conn, ctx: dict, draft_path: str, dry_run: bool) -> dict:
    """Mark the email_threads row replied + queue a follow-up. Replaces the old
    _mark_replied (which flipped a workflow stage nothing creates anymore)."""
    out = {"thread_updated": 0, "follow_up_queued": 0}
    if dry_run:
        return out
    now = datetime.now().isoformat(timespec="seconds")
    thread_id = ctx["thread_id"]
    try:
        if thread_id:
            cur = conn.execute(
                "UPDATE email_threads SET status='replied', intent_class=?, updated_at=? "
                "WHERE thread_id=?",
                (ctx["label"], now, thread_id),
            )
            out["thread_updated"] = cur.rowcount
        # Route into follow-up: one pending row per thread/email
        fq_thread = thread_id or ctx["email"]
        existing = conn.execute(
            "SELECT id FROM follow_up_queue WHERE thread_id=? AND status='pending' LIMIT 1",
            (fq_thread,),
        ).fetchone()
        if not existing:
            follow_up_at = (datetime.now() + timedelta(days=2)).isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO follow_up_queue (prospect_id, thread_id, follow_up_at, attempts, "
                "max_attempts, last_intent, status, draft_path, created_at, updated_at) "
                "VALUES (NULL, ?, ?, 0, 4, ?, 'pending', ?, ?, ?)",
                (fq_thread, follow_up_at, ctx["label"], draft_path, now, now),
            )
            out["follow_up_queued"] = 1
        conn.commit()
        # WS-F (2026-07-13): close the learning loop — link this reply back
        # to the originating outbound_jobs touch row(s) and credit the
        # subject/pitch variant a win in skill_variants. Deterministic
        # (lead_id = recipient email), shielded so a ledger failure never
        # blocks reply handling.
        try:
            from runtime.touch_log import mark_replied
            linked = mark_replied(conn, ctx["email"])
            out["touches_linked"] = len(linked)
        except Exception as exc:
            out["touch_link_error"] = str(exc)[:120]
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


# ---------------------------------------------------------------------------
# 4. Notify operator
# ---------------------------------------------------------------------------

def _fire_alert(conn, ctx: dict, row: dict, draft_path: str, dry_run: bool, verbose: bool) -> str:
    """Fire notify_operator_deduped with purpose=revenue (bypasses dedup). Returns result."""
    if dry_run:
        print(f"  [DRY-RUN] ALERT: 🎯 REPLY from {ctx['email']} | label={ctx['label']} | draft→{draft_path}")
        return "dry-run"
    try:
        from runtime.engine import notify_operator_deduped
    except ImportError:
        print("  [alert] ImportError: runtime.engine not found")
        return "import-error"

    preview = (row.get("body") or "")[:300].replace("\n", " ")
    text = (
        f"🎯 REPLY DETECTED — {ctx['name']} ({ctx['email']})\n"
        f"Label: {ctx['label']}\n"
        f"Preview: {preview}\n"
        f"Draft staged: {Path(draft_path).name if draft_path else '(draft failed)'} (review required — NO auto-send)\n"
        f"triage_id: {ctx['triage_id']}"
    )
    # workflow_id stays None: triage ids are not workflows rows and
    # notification_log.workflow_id has a FK to workflows(id).
    result = notify_operator_deduped(
        conn,
        text,
        kind="reply_watcher_hit",
        dedup_window_hours=168,
        workflow_id=None,
        lane="outreach",
        purpose="revenue",     # URGENT — bypasses dedup
    )
    return result


# ---------------------------------------------------------------------------
# 5. Digest update
# ---------------------------------------------------------------------------

def _update_digest(n_replies: int, watched: int) -> None:
    """Append a reply-watch line to today's activity digest / daily note."""
    today = datetime.now().strftime("%Y-%m-%d")
    digest_path = DATA_ROOT / "control" / "briefings" / f"reply-watch-{today}.md"
    line = f"Reply watch: {n_replies} replies (watching {watched} triage rows)\n"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    with digest_path.open("a", encoding="utf-8") as fh:
        fh.write(f"[{_now_iso()}] {line}")

    # Also append to daily note
    daily_note = DATA_ROOT / "memory" / f"{today}.md"
    if daily_note.exists():
        content = daily_note.read_text(encoding="utf-8")
        marker  = "## Reply Watch"
        if marker not in content:
            with daily_note.open("a", encoding="utf-8") as fh:
                fh.write(f"\n{marker}\n- {line}")
        else:
            lines = content.splitlines()
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].startswith("- Reply watch"):
                    lines[i] = f"- {line.strip()}"
                    break
            else:
                idx = next(i for i, l in enumerate(lines) if l.startswith(marker))
                lines.insert(idx + 1, f"- {line.strip()}")
            daily_note.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------

def run(dry_run: bool = False, verbose: bool = False) -> dict:
    import sqlite3

    state = _load_state()
    handled: dict[str, str] = state.get("handled", {})

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    try:
        rows = _load_watch_rows(handled, verbose=verbose)
        if not rows:
            if verbose:
                print("reply-watcher: no new classified human replies in triage")
            _log_event({"ts": _now_iso(), "event": "idle", "watched": 0})
            return {"watched": 0, "new_replies": 0, "dry_run": dry_run}

        new_replies = 0
        for row in rows:
            ctx = _row_ctx(row)
            if verbose:
                print(f"\n  *** REPLY: {ctx['email']} label={ctx['label']}")

            # Generate draft (smart-models invariant: opus-4-8 first)
            draft = _generate_draft(ctx, row.get("body") or "", ctx["label"], dry_run, verbose)
            draft_path = ""
            if draft:
                draft_path = _save_draft(ctx, draft, row) if not dry_run else "(dry-run)"
                if verbose:
                    print(f"  Draft saved: {draft_path}")
            elif verbose:
                print(f"  WARNING: Draft generation failed for {ctx['triage_id']}")

            alert_result = _fire_alert(conn, ctx, row, draft_path, dry_run, verbose)
            routing = _mark_thread_replied(conn, ctx, draft_path, dry_run)

            if not dry_run:
                handled[ctx["triage_id"]] = _now_iso()
            new_replies += 1

            _log_event({
                "ts":          _now_iso(),
                "event":       "reply_detected",
                "triage_id":   ctx["triage_id"],
                "email":       ctx["email"],
                "label":       ctx["label"],
                "thread_id":   ctx["thread_id"],
                "draft_path":  draft_path,
                "alert":       alert_result,
                "routing":     routing,
                "dry_run":     dry_run,
            })

        if not dry_run:
            _update_digest(new_replies, len(rows))
            state["handled"]  = handled
            state["last_run"] = _now_iso()
            _save_state(state)

        summary = {
            "ts":          _now_iso(),
            "watched":     len(rows),
            "new_replies": new_replies,
            "dry_run":     dry_run,
        }
        _log_event({"event": "run_summary", **summary})
        return summary

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Reply-watcher: 10-min triage fast-lane for human replies")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes / no alerts")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    # Kill-switch: set RICK_REPLY_WATCHER_LIVE=1 in rick.env to enable DB writes + alerts
    live_flag = os.getenv("RICK_REPLY_WATCHER_LIVE", "0").strip() == "1"
    if not live_flag and not args.dry_run:
        print("[reply-watcher] RICK_REPLY_WATCHER_LIVE not set — forcing dry-run. Set RICK_REPLY_WATCHER_LIVE=1 to enable.")
        args.dry_run = True

    result = run(dry_run=args.dry_run, verbose=args.verbose)
    mode   = "[DRY-RUN] " if args.dry_run else ""
    print(
        f"{mode}reply-watcher | "
        f"watched={result.get('watched', 0)} triage rows | "
        f"new_replies={result.get('new_replies', 0)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
