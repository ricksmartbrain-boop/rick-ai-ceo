#!/usr/bin/env python3
"""funnel-attribution.py — four-number weekly funnel signal.

The "did MRR move" question is a 30-day lagging indicator. This script
isolates *which step* leaks each week:

  1. Newsletter link CTR
       = recipients with last_event in {clicked} / total newsletter recipients
       Source: Resend /emails API (last_event field).
       NOTE: Resend's API gives per-recipient last_event, not per-link clicks.
       Per-link CTR requires the Resend webhook (events: email.clicked).
       This script computes recipient-level CTR as a usable proxy until the
       webhook is wired (see docs/funnel-attribution-setup.md).

  2. Pricing-page → Stripe-init rate
       = checkout.session.created events / pricing-page sessions
       Source: Stripe /v1/events for the numerator. Denominator (pricing-page
       sessions) requires a landing-page tracker — currently NONE wired, so
       this number reports `null` with reason "no_landing_tracker" until a
       tracker lands (see setup doc).

  3. Stripe-init → completion rate
       = checkout.session.completed / checkout.session.created
       Source: Stripe /v1/events (read-only, list endpoint).

  4. X-traffic share (added 2026-05-06, post X-account-restore)
       = (UTM-tagged inbound rows where utm_source startswith 'x') /
         (all UTM-tagged inbound rows in the window)
       Source: ~/rick-vault/operations/landing-pings.jsonl (same log #2 reads).
       Subtypes recognised: x, x_thread, x_reply, x_dm, x_bio.
       Answers: "is X actually driving traffic post-restore?"
       Status `no_landing_tracker` means the landing log is empty/absent.

CONSTRAINTS:
  - Read-only on Stripe (uses /v1/events list endpoint, never write APIs).
  - Read-only on Resend (uses /emails GET endpoint).
  - No new dashboards; --summary mode emits a 4-line block for piping into
    rick-roundup-weekly.py.
  - No LLM calls; data plumbing only. Smart-models invariant: N/A here.

OUTPUT:
  - Default: full JSON snapshot to stdout, append-write to
    ~/rick-vault/operations/funnel-attribution-YYYY-MM-DD.jsonl
  - --summary: 4-line markdown block for inline injection into roundup
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS = DATA_ROOT / "operations"
ENV_FILE = Path(os.getenv("RICK_ENV_FILE", str(Path.home() / "clawd" / "config" / "rick.env")))

STRIPE_EVENTS = "https://api.stripe.com/v1/events"
RESEND_EMAILS = "https://api.resend.com/emails"

# 7-day trailing window
WINDOW_DAYS = 7


def _load_env_var(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _http_get_json(url: str, headers: dict, timeout: int = 20) -> Optional[dict]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


# ──────────────────────────────────────────────────────────────
# Numbers
# ──────────────────────────────────────────────────────────────


def compute_newsletter_ctr(window_days: int = WINDOW_DAYS) -> dict:
    """Number 1: Newsletter link CTR (recipient-level proxy from Resend API).

    Counts recipients of newsletter campaigns (subject contains 'Rick Report'
    or 'Rick Roundup' OR utm_campaign in tracked link suggests newsletter)
    whose last_event is 'clicked'.

    Returns:
        dict with recipients, clicked, ctr_pct, plus method note.
    """
    api_key = _load_env_var("RESEND_API_KEY")
    if not api_key:
        return {
            "recipients": None,
            "clicked": None,
            "ctr_pct": None,
            "status": "no_resend_api_key",
            "note": "RESEND_API_KEY not set in env",
        }

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "rick-funnel-attribution/1.0",
        "Accept": "application/json",
    }

    recipients = 0
    clicked = 0
    opened = 0
    delivered = 0
    pages = 0
    before = None
    while pages < 20:
        url = f"{RESEND_EMAILS}?limit=100" + (f"&before={before}" if before else "")
        data = _http_get_json(url, headers)
        if data is None:
            return {
                "recipients": None,
                "clicked": None,
                "ctr_pct": None,
                "status": "resend_api_error",
                "note": "Resend /emails endpoint unreachable",
            }
        batch = data.get("data") or []
        if not batch:
            break
        for e in batch:
            created = e.get("created_at", "")
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue
            if created_dt < cutoff:
                # Stop paginating — Resend returns descending by date
                pages = 999
                break

            # Match newsletter-class campaigns by subject heuristic.
            # Pro broadcasts use "Rick Roundup", drafts use "Rick Report".
            subject = (e.get("subject") or "").lower()
            is_newsletter = (
                "rick report" in subject
                or "rick roundup" in subject
                or "weekly" in subject
                or "newsletter" in subject
            )
            if not is_newsletter:
                continue

            recipients += 1
            last_event = (e.get("last_event") or "").lower()
            if last_event == "clicked":
                clicked += 1
                opened += 1  # clicked implies opened
            elif last_event == "opened":
                opened += 1
            if last_event in {"delivered", "opened", "clicked"}:
                delivered += 1

        pages += 1
        if not data.get("has_more"):
            break
        before = batch[-1].get("id")

    ctr_pct = (clicked / recipients * 100) if recipients > 0 else None
    return {
        "recipients": recipients,
        "clicked": clicked,
        "opened": opened,
        "delivered": delivered,
        "ctr_pct": ctr_pct,
        "status": "ok" if recipients > 0 else "no_newsletter_sends_in_window",
        "method": "resend_api_last_event_proxy",
        "note": "Recipient-level CTR (proxy). Per-link CTR requires Resend webhook.",
    }


def _stripe_events_in_window(api_key: str, event_type: str, window_days: int) -> list[dict]:
    """List Stripe events of a given type in the trailing window."""
    since = int((datetime.now(timezone.utc) - timedelta(days=window_days)).timestamp())
    headers = {"Authorization": f"Bearer {api_key}"}
    events: list[dict] = []
    starting_after: Optional[str] = None
    pages = 0
    while pages < 10:  # cap at 1000 events / window
        params = [
            f"created[gte]={since}",
            "limit=100",
            f"types[]={urllib.parse.quote(event_type)}",
        ]
        if starting_after:
            params.append(f"starting_after={starting_after}")
        url = f"{STRIPE_EVENTS}?{'&'.join(params)}"
        data = _http_get_json(url, headers)
        if data is None:
            break
        batch = data.get("data") or []
        if not batch:
            break
        events.extend(batch)
        if not data.get("has_more"):
            break
        starting_after = batch[-1].get("id")
        pages += 1
    return events


def compute_pricing_to_init(window_days: int = WINDOW_DAYS) -> dict:
    """Number 2: Pricing-page → Stripe-init rate.

    Numerator: count of checkout.session.created events in window.
    Denominator: pricing-page sessions — REQUIRES a landing-page tracker
    that doesn't exist yet. We surface the numerator and explicitly
    flag the denominator gap so the docs path is unambiguous.
    """
    api_key = _load_env_var("STRIPE_SECRET_KEY")
    if not api_key:
        return {
            "checkout_sessions_started": None,
            "pricing_page_sessions": None,
            "rate_pct": None,
            "status": "no_stripe_api_key",
            "note": "STRIPE_SECRET_KEY not set in env",
        }
    events = _stripe_events_in_window(api_key, "checkout.session.created", window_days)
    started = len(events)

    # Denominator stub — no landing-page tracker exists yet.
    # When one lands (e.g. ~/rick-vault/operations/landing-pings.jsonl with
    # rows {ts, path, ref, utm_*}), this is where we'd count rows where
    # path startswith '/pricing' in the trailing window.
    landing_log = OPS / "landing-pings.jsonl"
    pricing_sessions = None
    landing_status = "no_landing_tracker"
    if landing_log.exists():
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        count = 0
        try:
            with landing_log.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (row.get("ts") or "") < cutoff_iso:
                        continue
                    path = (row.get("path") or "").lower()
                    if path.startswith("/pricing"):
                        count += 1
            pricing_sessions = count
            landing_status = "ok"
        except OSError:
            landing_status = "landing_log_unreadable"

    rate_pct = None
    if pricing_sessions is not None and pricing_sessions > 0:
        rate_pct = started / pricing_sessions * 100

    return {
        "checkout_sessions_started": started,
        "pricing_page_sessions": pricing_sessions,
        "rate_pct": rate_pct,
        "status": landing_status,
        "note": (
            "checkout.session.created from Stripe /v1/events. "
            "Denominator needs landing-page tracker — see docs/funnel-attribution-setup.md."
        ),
    }


def compute_x_traffic_share(window_days: int = WINDOW_DAYS) -> dict:
    """Number 4: X-traffic share of UTM-tagged inbound this week.

    Reads ~/rick-vault/operations/landing-pings.jsonl (same source as #2's
    denominator). For every row in window with a non-empty utm_source, classify
    as `x_*` if the source matches a known X subtype (x, x_thread, x_reply,
    x_dm, x_bio) — otherwise `non_x`. Returns the ratio + per-subtype breakdown.

    If landing-pings.jsonl is missing/empty, returns status=no_landing_tracker
    so the summary line surfaces the gap rather than reporting a fake 0%.
    """
    # Locally enumerate to avoid importing runtime/utm.py from a script that
    # may run in stripped envs (Railway, cron with minimal PYTHONPATH).
    x_subsources = {"x", "x_thread", "x_reply", "x_dm", "x_bio"}

    landing_log = OPS / "landing-pings.jsonl"
    if not landing_log.exists():
        return {
            "x_total": None,
            "all_utm_total": None,
            "share_pct": None,
            "by_subtype": {},
            "status": "no_landing_tracker",
            "note": "landing-pings.jsonl missing — same blocker as funnel #2.",
        }

    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    by_subtype: dict[str, int] = {}
    x_total = 0
    all_utm_total = 0
    try:
        with landing_log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (row.get("ts") or "") < cutoff_iso:
                    continue
                src = (row.get("utm_source") or "").strip().lower()
                if not src:
                    continue
                all_utm_total += 1
                if src in x_subsources:
                    x_total += 1
                    by_subtype[src] = by_subtype.get(src, 0) + 1
    except OSError:
        return {
            "x_total": None,
            "all_utm_total": None,
            "share_pct": None,
            "by_subtype": {},
            "status": "landing_log_unreadable",
        }

    share_pct = (x_total / all_utm_total * 100) if all_utm_total > 0 else None
    return {
        "x_total": x_total,
        "all_utm_total": all_utm_total,
        "share_pct": share_pct,
        "by_subtype": by_subtype,
        "status": "ok" if all_utm_total > 0 else "no_utm_inbound_in_window",
        "note": (
            "X subtypes: x, x_thread, x_reply, x_dm, x_bio. "
            "Operator-paced X posting — first 30 days post-restore are manual."
        ),
    }


def compute_init_to_completion(window_days: int = WINDOW_DAYS) -> dict:
    """Number 3: Stripe-init → completion rate. Pure Stripe Events API."""
    api_key = _load_env_var("STRIPE_SECRET_KEY")
    if not api_key:
        return {
            "started": None,
            "completed": None,
            "rate_pct": None,
            "status": "no_stripe_api_key",
        }
    started_events = _stripe_events_in_window(api_key, "checkout.session.created", window_days)
    completed_events = _stripe_events_in_window(api_key, "checkout.session.completed", window_days)
    started = len(started_events)
    completed = len(completed_events)
    rate_pct = (completed / started * 100) if started > 0 else None
    return {
        "started": started,
        "completed": completed,
        "rate_pct": rate_pct,
        "status": "ok" if started > 0 else "no_checkouts_started_in_window",
        "note": (
            "Note: completed sessions in window may include sessions started "
            "before the window — rate is directional, not exact cohort."
        ),
    }


# ──────────────────────────────────────────────────────────────
# Source breakdown — recognise every utm_source we set ourselves
# ──────────────────────────────────────────────────────────────

KNOWN_SOURCES = {
    # X surfaces (manual/ @stbelkins; @MeetRickAI suspended 2026-05-06)
    "x", "x_thread", "x_post", "x_reply", "x_dm", "x_bio", "x_share",
    # Coordinated launch (5/13)
    "hn", "showhn",
    "ih", "indiehackers",
    "reddit",
    "linkedin", "linkedin_share",
    "ph", "producthunt",
    # Tools
    "founder_tax",
    "roast",
    "this_week",
    "pilot",
    # Email/newsletter
    "newsletter", "nurture", "broadcast",
    # Cold email by industry (extend as we add)
    "cold_email_medspa", "cold_email_dentist", "cold_email_barber", "cold_email_chiro",
    "cold_email_lawfirm", "cold_email_fitness",
    # Product surfaces
    "kit", "agents_kit", "playbook", "pricing",
    # Direct/organic
    "direct", "organic", "search",
}


def compute_utm_source_breakdown(window_days: int = WINDOW_DAYS) -> dict:
    """Number 5: per-utm_source inbound count for the window.

    Reads ~/rick-vault/operations/landing-pings.jsonl. Returns counts per
    source plus 'unknown' for any source not in KNOWN_SOURCES (so we catch
    typos/drift). If the landing tracker is missing, returns status accordingly.
    """
    landing_log = OPS / "landing-pings.jsonl"
    if not landing_log.exists():
        return {
            "by_source": {},
            "unknown_sources": [],
            "total": None,
            "status": "no_landing_tracker",
            "note": "landing-pings.jsonl missing — same blocker as funnel #2/#4.",
        }
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    by_source: dict[str, int] = {}
    unknown: dict[str, int] = {}
    total = 0
    try:
        with landing_log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (row.get("ts") or "") < cutoff_iso:
                    continue
                src = (row.get("utm_source") or "").strip().lower()
                if not src:
                    continue
                total += 1
                if src in KNOWN_SOURCES:
                    by_source[src] = by_source.get(src, 0) + 1
                else:
                    unknown[src] = unknown.get(src, 0) + 1
    except OSError:
        return {
            "by_source": {},
            "unknown_sources": [],
            "total": None,
            "status": "landing_log_unreadable",
        }
    return {
        "by_source": by_source,
        "unknown_sources": sorted(unknown.items(), key=lambda kv: -kv[1]),
        "total": total,
        "status": "ok" if total > 0 else "no_utm_inbound_in_window",
        "note": "Tracks all utm_source values; unknown = drift/typo to investigate.",
    }


# ──────────────────────────────────────────────────────────────
# Snapshot + render
# ──────────────────────────────────────────────────────────────


def build_snapshot(window_days: int = WINDOW_DAYS) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "ts": now.isoformat(timespec="seconds"),
        "window_days": window_days,
        "window_start": (now - timedelta(days=window_days)).isoformat(timespec="seconds"),
        "newsletter_ctr": compute_newsletter_ctr(window_days),
        "pricing_to_init": compute_pricing_to_init(window_days),
        "init_to_completion": compute_init_to_completion(window_days),
        "x_traffic_share": compute_x_traffic_share(window_days),
        "utm_source_breakdown": compute_utm_source_breakdown(window_days),
    }


def render_summary(snap: dict) -> str:
    """5-line markdown block for piping into the weekly roundup."""
    n1 = snap["newsletter_ctr"]
    n2 = snap["pricing_to_init"]
    n3 = snap["init_to_completion"]
    n4 = snap.get("x_traffic_share") or {}

    def _fmt_pct(val):
        return f"{val:.1f}%" if isinstance(val, (int, float)) else "—"

    def _fmt_ratio(num, den):
        if num is None or den is None:
            if num is not None:
                return f"{num} / —"
            return "— / —"
        return f"{num} / {den}"

    n1_line = (
        f"Newsletter CTR: {_fmt_pct(n1.get('ctr_pct'))} "
        f"({_fmt_ratio(n1.get('clicked'), n1.get('recipients'))})"
    )
    if n1.get("status") not in ("ok",):
        n1_line += f" [{n1.get('status')}]"

    n2_line = (
        f"Pricing → Stripe-init: {_fmt_pct(n2.get('rate_pct'))} "
        f"({_fmt_ratio(n2.get('checkout_sessions_started'), n2.get('pricing_page_sessions'))})"
    )
    if n2.get("status") != "ok":
        n2_line += f" [{n2.get('status')}]"

    n3_line = (
        f"Stripe-init → completion: {_fmt_pct(n3.get('rate_pct'))} "
        f"({_fmt_ratio(n3.get('completed'), n3.get('started'))})"
    )
    if n3.get("status") != "ok":
        n3_line += f" [{n3.get('status')}]"

    # Number 4 — X-traffic share. Format: "X-traffic share: 14.3% (3/21) — thread:2 dm:1"
    by_sub = n4.get("by_subtype") or {}
    sub_breakdown = " ".join(f"{k.replace('x_', '').replace('x', 'all')}:{v}"
                             for k, v in sorted(by_sub.items(), key=lambda x: -x[1])[:5])
    n4_line = (
        f"X-traffic share: {_fmt_pct(n4.get('share_pct'))} "
        f"({_fmt_ratio(n4.get('x_total'), n4.get('all_utm_total'))})"
    )
    if sub_breakdown:
        n4_line += f" — {sub_breakdown}"
    if n4.get("status") not in ("ok",):
        n4_line += f" [{n4.get('status')}]"

    header = f"**4-number funnel ({snap['window_days']}d, {snap['ts'][:10]})**"
    return "\n".join([header, f"- {n1_line}", f"- {n2_line}", f"- {n3_line}", f"- {n4_line}"])


def write_snapshot(snap: dict) -> Path:
    OPS.mkdir(parents=True, exist_ok=True)
    date_str = snap["ts"][:10]
    path = OPS / f"funnel-attribution-{date_str}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, sort_keys=True) + "\n")
    return path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--summary", action="store_true",
                   help="Emit only the 4-line markdown summary (for digest piping)")
    p.add_argument("--json", action="store_true", dest="json_only",
                   help="JSON output only — no append-write")
    p.add_argument("--no-write", action="store_true", help="Skip jsonl append")
    p.add_argument("--window-days", type=int, default=WINDOW_DAYS, help="Trailing window in days")
    args = p.parse_args()

    snap = build_snapshot(window_days=args.window_days)

    if args.summary:
        print(render_summary(snap))
    elif args.json_only:
        print(json.dumps(snap, indent=2))
    else:
        # Default: full JSON to stdout
        print(json.dumps(snap, indent=2))

    if not args.json_only and not args.no_write:
        try:
            write_snapshot(snap)
        except OSError as exc:
            print(f"[funnel-attribution] snapshot write failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
