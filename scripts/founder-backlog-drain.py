#!/usr/bin/env python3
"""founder-backlog-drain.py — draft kept-but-undrafted hn-showhn prospects.

The founder-sourcer (scripts/founder-sourcer.py) only drafts leads it
sourced in the SAME run, so qualified prospects stored in prospect_pipeline
(platform='hn-showhn', status='sourced-founder') that missed their run's
draft budget sit undrafted forever. This driver replays the sourcer's OWN
drafting step over that backlog — same gates, same outbox contract, same
touch ledger — in daily-cap-sized batches.

Reuses founder-sourcer functions directly (no forked logic):
  fetch_author_contact         HN 'about' email (sourcer's 1st-choice source)
  fetch_site_facts / qualify   re-fetch + re-score before spending LLM
  draft_email                  LLM body via runtime.llm route=writing
  _apply_subject_variant       WS-F Thompson-picked subject
  write_outbox_draft           standard gated outbox .json (cold, +2h)
  log_touch                    WS-F ledger row (status='queued')
  _hold_drafts                 fail-loud hold when no preview ping goes out

Most backlog rows were stored WITHOUT an email (the sourcer keeps
qualified prospects it can't contact yet). This driver replays the
sourcer's own contact discovery for them — HN profile 'about' email
first, then a plainly-published product-site address — and only drafts
when a discovered email passes every gate. Never guesses emails.

Safety (mirrors the sourcer):
  - NEVER sends. Drafts are written to the outbox and immediately flipped
    to status 'held' because this driver sends no Telegram preview ping —
    the sourcer's own rule: no owner veto ping => held, never pending.
    Release = owner/orchestrator review flips status to 'pending'.
  - Shares the sourcer's HARD 10/day cold-draft cap (state file + outbox
    file count for today), so backlog + daily sourcing never exceed it.
  - Re-applies every current source gate: role-account, placeholder
    domain, code-forge localpart/host, suppression, 7d frequency, dedupe
    vs outbound history and outbox/sent drafts.
  - Excludes the synthetic test persona (arjun / rtrvr.ai).

CLI:
  --dry-run          list what would be drafted, write nothing
  --max-drafts N     cap this run (default/hard cap: sourcer's 10/day)
  --max-site-fetch N max product sites fetched per run (default 25,
                     same per-run budget as the sourcer)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# founder-sourcer.py has a hyphen — import it by path, sharing its env
# bootstrap, constants and helpers.
_FS_PATH = Path(__file__).resolve().parent / "founder-sourcer.py"
_spec = importlib.util.spec_from_file_location("founder_sourcer", _FS_PATH)
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)

# Synthetic test persona (2026-07-18 audit): never target, treat as noise.
SYNTHETIC_MARKERS = ("arjun", "rtrvr")


def _notes(row) -> dict:
    try:
        data = json.loads(row["notes"] or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _outbox_sent_recipients() -> set[str]:
    """Every 'to' in outbox (recursive: held/archive subdirs too) + sent."""
    recipients: set[str] = set()
    for base, pattern in ((fs.OUTBOX_DIR, "**/*.json"), (fs.SENT_DIR, "*.json")):
        if not base.exists():
            continue
        for f in base.glob(pattern):
            try:
                msg = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if msg.get("to"):
                recipients.add(str(msg["to"]).strip().lower())
    return recipients


def main() -> int:
    ap = argparse.ArgumentParser(description="Draft the hn-showhn backlog (gated, capped)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-drafts", type=int, default=fs.DAILY_DRAFT_CAP)
    ap.add_argument("--max-site-fetch", type=int, default=25)
    args = ap.parse_args()

    fs._ensure_dirs()
    state = fs._read_state()
    today = datetime.now().strftime("%Y-%m-%d")
    drafts_today = int(state.get("drafts_by_day", {}).get(today, 0))
    on_disk_today = len(list(fs.OUTBOX_DIR.glob(f"founder-*-{datetime.now():%Y%m%d}.json")))
    drafts_today = max(drafts_today, on_disk_today)
    draft_budget = max(0, min(args.max_drafts, fs.DAILY_DRAFT_CAP) - drafts_today)

    fs._log("backlog.run_start", dry_run=args.dry_run, drafts_today=drafts_today,
            draft_budget=draft_budget)

    from runtime.db import connect as runtime_connect
    from runtime.email_validator import is_placeholder_domain, is_role_account
    from runtime.kill_switches import is_suppressed_address, last_send_ts
    from runtime.touch_log import log_touch

    conn = runtime_connect()
    outbound_blob = "\n".join(
        (r[0] or "") for r in conn.execute("SELECT payload_json FROM outbound_jobs")
    ).lower()
    drafted_recipients = _outbox_sent_recipients()

    rows = conn.execute(
        """
        SELECT id, username, profile_url, score, notes
          FROM prospect_pipeline
         WHERE platform = 'hn-showhn' AND status = 'sourced-founder'
         ORDER BY score DESC, updated_at DESC
        """
    ).fetchall()

    def email_gate(email: str, domain: str) -> str:
        """Return '' when the address may be drafted, else the gate name.
        Same gates, same order as founder-sourcer main() applies at source."""
        if any(m in email + " " + domain for m in SYNTHETIC_MARKERS):
            return "synthetic"
        if email in drafted_recipients or email in outbound_blob:
            return "already_drafted"
        localpart = email.split("@", 1)[0]
        if "github" in localpart or "gitlab" in localpart:
            return "forge_email"
        if is_role_account(email):
            return "role_account"
        if is_placeholder_domain(email):
            return "placeholder_domain"
        if is_suppressed_address(email):
            return "suppressed"
        last = last_send_ts(email)
        if last is not None and (datetime.now() - last.replace(tzinfo=None)) < timedelta(days=7):
            return "frequency_7d"
        return ""

    counts = {"backlog": len(rows), "already_drafted": 0, "gated": 0,
              "no_email_found": 0, "unqualified": 0, "fetch_budget_left": 0,
              "drafted": 0, "held": 0}
    candidates: list[dict] = []
    for row in rows:
        notes = _notes(row)
        email = str(notes.get("email") or "").strip().lower()
        domain = str(notes.get("domain") or "").strip().lower()
        if domain in fs.HOST_BLOCKLIST or domain.endswith((".github.io", ".gitlab.io")):
            counts["gated"] += 1
            fs._log("backlog.skip_host", domain=domain)
            continue
        if domain and domain in outbound_blob:
            counts["already_drafted"] += 1
            continue
        if email:
            gate = email_gate(email, domain)
            if gate == "already_drafted":
                counts["already_drafted"] += 1
                continue
            if gate:
                counts["gated"] += 1
                fs._log("backlog.skip_" + gate, domain=domain)
                continue
        candidates.append({
            "id": row["id"],
            "author": row["username"] or "",
            "url": row["profile_url"] or "",
            "title": str(notes.get("story_title") or domain),
            "hn_score": int(notes.get("hn_score") or 0),
            "domain": domain,
            "email": email,  # may be "" — discovery happens at fetch time
            "contact_source": str(notes.get("contact_source") or ""),
            "stored_score": float(row["score"] or 0),
        })

    # Rows that already carry a contact first — they need no discovery luck.
    candidates.sort(key=lambda l: (not l["email"], -l["stored_score"], -l["hn_score"]))
    fs._log("backlog.candidates", count=len(candidates))

    drafted_paths: list[Path] = []
    fetches = 0
    processed = 0
    for lead in candidates:
        if counts["drafted"] >= draft_budget:
            break
        if fetches >= args.max_site_fetch:
            counts["fetch_budget_left"] += 1
            continue
        fetches += 1
        processed += 1
        # Re-fetch + re-qualify with TODAY's heuristics before spending an
        # LLM call — stored scores can be stale (site changed, robots, ...).
        facts = fs.fetch_site_facts(lead["url"])
        if not lead["email"]:
            # Sourcer's own contact discovery (main() order): HN profile
            # 'about' email first, then plainly-published site contact.
            author = fs.fetch_author_contact(lead["author"])
            if author.get("email"):
                lead["email"], lead["contact_source"] = author["email"], "hn-profile-about"
            elif facts.get("emails"):
                lead["email"], lead["contact_source"] = facts["emails"][0], "product-site"
            if not lead["email"]:
                counts["no_email_found"] += 1
                fs._log("backlog.no_email_found", domain=lead["domain"])
                continue
            lead["email"] = lead["email"].strip().lower()
            gate = email_gate(lead["email"], lead["domain"])
            if gate:
                counts["already_drafted" if gate == "already_drafted" else "gated"] += 1
                fs._log("backlog.skip_" + gate, domain=lead["domain"])
                continue
        story = {"title": lead["title"], "hn_score": lead["hn_score"]}
        score, reasons = fs.qualify(story, facts, lead["email"])
        if score < 3:
            counts["unqualified"] += 1
            fs._log("backlog.requalify_below_bar", domain=lead["domain"],
                    score=score, reasons=reasons)
            continue
        lead["score"], lead["reasons"], lead["facts"] = score, reasons, facts
        if args.dry_run:
            counts["drafted"] += 1
            print(f"DRY-RUN would draft: {lead['email']} ({lead['domain']}) score={score}")
            continue
        body = fs.draft_email(lead, facts)
        body, subject_variant = fs._apply_subject_variant(conn, body, lead)
        path = fs.write_outbox_draft(lead, body, subject_variant)
        drafted_paths.append(path)
        counts["drafted"] += 1
        subject = next(
            (ln.replace("**Subject:**", "").strip()
             for ln in body.splitlines() if ln.startswith("**Subject:**")),
            "",
        )
        try:
            log_touch(
                conn, to=lead["email"], channel="email",
                template_id=f"founder:{subject_variant or 'llm_subject'}",
                subject=subject, variant=subject_variant,
                skill="founder_outreach_subject" if subject_variant else "",
                source="founder-backlog-drain", status="queued",
                outbox_file=path.name,
            )
        except Exception as e:  # ledger failure never blocks drafting
            fs._log("backlog.touch_log_error", domain=lead["domain"], error=str(e)[:120])
        fs._log("backlog.drafted", domain=lead["domain"], to=lead["email"][:80],
                file=str(path), score=score, variant=subject_variant)

    if drafted_paths:
        # HARD RULE (same as the sourcer): drafts only go 'pending' behind an
        # owner preview ping. This driver sends none, so every draft is held
        # for explicit review — flip status to 'pending' to release.
        fs._hold_drafts(drafted_paths, "backlog_drain_no_preview_ping_owner_review")
        counts["held"] = len(drafted_paths)
        state.setdefault("drafts_by_day", {})[today] = drafts_today + counts["drafted"]
        fs._write_state(state)

    # Candidates never reached this run (draft budget hit or fetch budget
    # spent) — they stay in the pipeline for the next batched run.
    remaining = max(0, len(candidates) - processed)
    summary = {"ran_at": fs.now_iso(), **counts,
               "candidates": len(candidates),
               "remaining_backlog": remaining,
               "dry_run": args.dry_run}
    print(json.dumps(summary, indent=2))
    fs._log("backlog.run_done", **counts, remaining=max(0, remaining), dry_run=args.dry_run)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
