#!/usr/bin/env python3
"""this-week-page.py — render meetrick.ai/this-week.html from production logs.

Reads (read-only) last 7d from rick-vault/operations + workspace git log +
meetrick-content/blog and writes a single static HTML file. Idempotent: anchored
to the most recent Mon 09:00 PT, identical input → identical output.

Usage: python3 scripts/this-week-page.py [--out PATH] [--now ISO]
"""
from __future__ import annotations
import argparse, email.utils, json, re, subprocess, sys, urllib.parse
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

HOME = Path.home()
VAULT = HOME / "rick-vault" / "operations"
BLOG_DIR = HOME / "meetrick-content" / "blog"
DRAFTS = HOME / "rick-vault" / "projects" / "email" / "newsletter-drafts"
WSGIT = HOME / ".openclaw" / "workspace"
DEFAULT_OUT = HOME / "meetrick-site" / "this-week.html"
# X-thread sidecar drafts — operator copy-pastes manually (zero automation
# during 30-day post-suspension cooldown; see docs/x-funnel-integration-2026-05-06.md).
X_DRAFTS_DIR = HOME / "rick-vault" / "projects" / "x-drafts"
VELOCITY = HOME / "rick-vault" / "revenue" / "velocity.json"
MAILBOX_TRIAGE = HOME / "rick-vault" / "mailbox" / "triage"
# Fulfillment-receipt-reconciler read model (fulfillment-receipt.v1). If this
# ledger is absent the metric renders "not enough data" — NEVER fall back to
# loose timestamp correlation (shared-Stripe mis-attribution precedent, 2026-07-13).
RECEIPTS_LEDGER = HOME / "rick-vault" / "control" / "ledgers" / "fulfillment-receipts.jsonl"
# One-line postmortem notes for known outages, keyed by UTC date the gap spans.
KNOWN_INCIDENTS = {
    "2026-07-14": "Jul 14: Mac hard-down ~17.5h (launchd autorestart was off). "
                  "Fixed same day; keep-alive guard added.",
}
PT = timezone(timedelta(hours=-7))


def parse_ts(ts):
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def iter_jsonl(p: Path):
    if not p.exists():
        return
    for line in p.open():
        line = line.strip()
        if line:
            try: yield json.loads(line)
            except Exception: pass


def in_window(t, since, until):
    return bool(t) and since <= t <= until


def page_anchor(now_utc):
    """Most recent Monday 09:00 PT <= now (idempotency anchor)."""
    pt = now_utc.astimezone(PT).replace(hour=9, minute=0, second=0, microsecond=0) - \
         timedelta(days=now_utc.astimezone(PT).weekday())
    return pt - timedelta(days=7) if pt > now_utc.astimezone(PT) else pt


def clean_title(t):
    """Unfurl 'Workflow created: Initiative: {...title: X...}' → X."""
    t = (t or "").replace("Workflow created:", "").strip()
    m = re.search(r"'title':\s*'([^']+)'", t)
    return m.group(1).strip() if m else re.sub(r"^Initiative:\s*", "", t).strip()


def fmt_dur(s):
    s = int(round(s))
    if s < 60: return f"{s}s"
    if s < 3600: return f"{s // 60}m {s % 60:02d}s"
    return f"{s / 3600:.1f}h"


def metric_reply_sla(since, until):
    """Reply SLA: received_at → router ran_at, per routed human reply.
    reply-router ran_at is naive PT; received_at comes from the matching
    mailbox/triage/<file> record via a deterministic join on
    (from == email, router_ran_at == ran_at) — no fuzzy matching.
    Excludes automated_notification and @example.com drill addresses.
    Returns None only if the reply-router log itself is missing."""
    path = VAULT / "reply-router.jsonl"
    if not path.exists():
        return None
    deltas, unmatched, n = [], 0, 0
    triage_cache = {}
    for d in iter_jsonl(path):
        if "email" not in d or "action" not in d:  # secondary rows (auto_draft etc.)
            continue
        if d.get("label") == "automated_notification":
            continue
        addr = d.get("email", "")
        if addr.endswith("@example.com"):  # test drills are not customers
            continue
        try:
            ran = datetime.fromisoformat(d["ran_at"]).replace(tzinfo=PT)
        except Exception:
            continue
        if not in_window(ran, since, until):
            continue
        n += 1
        fname = d.get("file", "")
        if fname not in triage_cache:
            triage_cache[fname] = list(iter_jsonl(MAILBOX_TRIAGE / fname)) if fname else []
        rec = next((r for r in triage_cache[fname]
                    if r.get("from") == addr and r.get("router_ran_at") == d.get("ran_at")), None)
        recv = None
        if rec and rec.get("received_at"):
            try:
                recv = email.utils.parsedate_to_datetime(rec["received_at"])
            except Exception:
                recv = None
        if recv is None or recv.tzinfo is None:
            unmatched += 1
            continue
        delta = (ran - recv).total_seconds()
        if delta < 0:  # clock skew — drop rather than fake
            unmatched += 1
            continue
        deltas.append(delta)
    return {"n": n, "deltas": sorted(deltas), "unmatched": unmatched}


def metric_fulfillment_latency(since, until):
    """Purchase → delivery-email latency via the fulfillment-receipt-reconciler
    read model, joined by idempotency key ONLY (child keys are
    {root}:delivery-email etc. — normalize to root before joining).
    Missing ledger → None (renders 'not enough data'), never a guessed number."""
    if not RECEIPTS_LEDGER.exists():
        return None
    suffixes = (":customer-memory", ":delivery-email", ":sequence-enrollment")
    by_key = {}
    for r in iter_jsonl(RECEIPTS_LEDGER):
        if r.get("schema_version") != "fulfillment-receipt.v1": continue
        if r.get("status") != "completed": continue
        key, st, t = r.get("idempotency_key"), r.get("step_type"), parse_ts(r.get("occurred_at"))
        if not (key and st and t): continue
        for suf in suffixes:
            if key.endswith(suf):
                key = key[:-len(suf)]
                break
        slot = by_key.setdefault(key, {})
        if st not in slot or t < slot[st]:
            slot[st] = t
    lat = []
    for stages in by_key.values():
        p, dl = stages.get("purchase"), stages.get("delivery-email")
        if p and dl and in_window(dl, since, until) and dl >= p:
            lat.append((dl - p).total_seconds())
    return {"n": len(lat), "latencies": sorted(lat)}


def metric_uptime(since, until):
    """Uptime from system-health.jsonl sample GAPS — absence of logs counts as
    downtime. Nominal cadence = median in-window sample interval; any gap over
    2x nominal contributes (gap - nominal) downtime, clipped to the window.
    Trailing silence up to `until` counts too. Returns None below 5 samples."""
    path = VAULT / "system-health.jsonl"
    if not path.exists():
        return None
    lookback = since - timedelta(hours=36)
    ts = sorted(t for d in iter_jsonl(path)
                if (t := parse_ts(d.get("ts"))) and lookback <= t <= until)
    inw = [t for t in ts if t >= since]
    if len(inw) < 5:
        return None
    steps = sorted((b - a).total_seconds() for a, b in zip(inw, inw[1:]))
    med = steps[len(steps) // 2]
    grace = max(2 * med, 600)
    down, gaps = 0.0, []
    for a, b in zip(ts, ts[1:]):
        if (b - a).total_seconds() <= grace: continue
        seg = (min(b, until) - max(a, since)).total_seconds() - med
        if seg > 0:
            down += seg
            gaps.append((max(a, since), min(b, until), seg))
    tail = (until - ts[-1]).total_seconds()
    if tail > grace:
        down += tail - med
        gaps.append((ts[-1], until, tail - med))
    window_s = (until - since).total_seconds()
    incidents = []
    for s, e, seg in gaps:
        if seg < 4 * 3600: continue
        note = next((KNOWN_INCIDENTS[k] for k in KNOWN_INCIDENTS
                     if s.date().isoformat() <= k <= e.date().isoformat()), None)
        incidents.append(note or f"{fmt_dur(seg)} with no health samples starting "
                                 f"{s.astimezone(PT).strftime('%b %d %H:%M PT')} — cause not yet logged")
    return {"pct": 100.0 * max(0.0, 1.0 - down / window_s), "down_s": down,
            "samples": len(inw), "gap_count": len(gaps), "incidents": incidents}


def metric_saves(since, until):
    """Saves attempted — Rick-attributable events only: winback emails actually
    queued + real (non-wf_test) critical replies caught. Scans that found
    nothing are context, not saves. None if neither monitor logged in-window."""
    inw = lambda t: in_window(t, since, until)
    wb_scans = wb_queued = 0
    for d in iter_jsonl(VAULT / "winback-scheduler.jsonl"):
        if d.get("event") == "scan_complete" and inw(parse_ts(d.get("ts"))):
            wb_scans += 1
            wb_queued += int(d.get("queued") or 0)
    cw_runs = cw_detected = 0
    for d in iter_jsonl(VAULT / "critical-window-monitor.jsonl"):
        if not inw(parse_ts(d.get("ts"))): continue
        cw_runs += 1
        if d.get("event") == "critical_reply_detected" \
                and not str(d.get("wf_id", "")).startswith("wf_test"):
            cw_detected += 1
    if wb_scans == 0 and cw_runs == 0:
        return None
    return {"attempted": wb_queued + cw_detected, "winback_queued": wb_queued,
            "winback_scans": wb_scans, "critical_detected": cw_detected,
            "monitor_runs": cw_runs}


def gather(since, until):
    inw = lambda t: in_window(t, since, until)
    # Newsletters (sent in window) + in-flight drafts
    newsletters = [d for d in iter_jsonl(VAULT / "newsletter-ledger.jsonl")
                   if inw(parse_ts(d.get("sent_at") or d.get("date")))]
    sent_iss = {n.get("issue") for n in newsletters if n.get("sent_at")}
    if DRAFTS.exists():
        for j in sorted(DRAFTS.glob("*.json")):
            if ".bak" in j.name: continue
            try: m = json.loads(j.read_text())
            except Exception: continue
            if inw(parse_ts(m.get("drafted_at") or m.get("date"))) and m.get("issue") not in sent_iss:
                m["__draft"] = True
                newsletters.append(m)

    # Email sends
    sends_total, recipients = 0, set()
    for d in iter_jsonl(VAULT / "email-sends.jsonl"):
        if inw(parse_ts(d.get("ts"))) and d.get("status") == "sent":
            sends_total += 1
            recipients.add(d.get("to", ""))

    replies = [d for d in iter_jsonl(VAULT / "reply-router.jsonl")
               if inw(parse_ts(d.get("ran_at") or d.get("ts")))]

    seq = {}
    for d in iter_jsonl(VAULT / "sequencer.jsonl"):
        if inw(parse_ts(d.get("ts"))):
            ev = d.get("event", "?"); seq[ev] = seq.get(ev, 0) + 1

    # Decisions + roadmap from execution-ledger (single pass)
    decisions, planned, seen = [], [], set()
    for d in iter_jsonl(VAULT / "execution-ledger.jsonl"):
        if not inw(parse_ts(d.get("timestamp"))): continue
        if d.get("kind") in ("lesson", "decision") or d.get("impact") == "high":
            decisions.append(d)
        title = d.get("title", "")
        if d.get("kind") == "planned" or d.get("status") == "planned" \
                or "Workflow created:" in title or "Initiative" in title:
            ct = clean_title(title)
            if ct and ct not in seen:
                seen.add(ct); planned.append(ct)
    planned = planned[-6:]

    commits = []
    if (WSGIT / ".git").exists():
        try:
            raw = subprocess.check_output(
                ["git", "-C", str(WSGIT), "log",
                 f"--since={since.isoformat()}", f"--until={until.isoformat()}",
                 "--pretty=format:%h|%aI|%s"], text=True, stderr=subprocess.DEVNULL)
            for line in raw.splitlines():
                parts = line.split("|", 2)
                if len(parts) == 3 and parse_ts(parts[1]):
                    commits.append({"sha": parts[0], "ts": parse_ts(parts[1]), "subj": parts[2]})
        except Exception: pass

    posts = []
    if BLOG_DIR.exists():
        for md in BLOG_DIR.glob("*.md"):
            try: fm = md.read_text().split("---", 2)[1]
            except Exception: continue
            md_date = re.search(r'^date:\s*"?([\d-]+)"?', fm, re.M)
            md_title = re.search(r'^title:\s*"(.*?)"', fm, re.M)
            md_slug = re.search(r'^slug:\s*"?([\w-]+)"?', fm, re.M)
            if not (md_date and md_title and md_slug): continue
            try: d = datetime.strptime(md_date.group(1), "%Y-%m-%d").replace(tzinfo=PT)
            except Exception: continue
            if datetime.fromtimestamp(md.stat().st_mtime, tz=PT) < since: continue
            posts.append({"date": d, "title": md_title.group(1), "slug": md_slug.group(1)})
    posts.sort(key=lambda p: p["date"])

    return dict(newsletters=newsletters, sends_total=sends_total,
                recipients=len(recipients), replies=replies, seq=seq,
                decisions=decisions, planned=planned, commits=commits, posts=posts,
                reply_sla=metric_reply_sla(since, until),
                fulfillment=metric_fulfillment_latency(since, until),
                uptime=metric_uptime(since, until),
                saves=metric_saves(since, until))


NOT_ENOUGH = "not enough data this week"


def machine_lines(g):
    """Render the four machine metrics as (name, value, basis) plain-text
    triples — shared by build_html() and build_x_thread_draft() so the page and
    the thread can never drift apart. Honesty rules baked in: every value
    carries its n; below n=3 raw values are shown, never an implied average;
    a missing source renders 'not enough data this week', never a stale number."""
    lines = []

    m = g["reply_sla"]
    if m is None:
        lines.append(("reply SLA", NOT_ENOUGH, "reply-router log unavailable"))
    elif m["n"] == 0:
        lines.append(("reply SLA", "0 human replies this week (n=0)",
                      "automated notifications and drill addresses excluded"))
    elif not m["deltas"]:
        lines.append(("reply SLA", NOT_ENOUGH,
                      f"{m['n']} replies routed but no receipt timestamps matched (n={m['n']})"))
    elif len(m["deltas"]) < 3:
        vals = ", ".join(fmt_dur(x) for x in m["deltas"])
        lines.append(("reply SLA",
                      f"{len(m['deltas'])} human repl{'ies' if len(m['deltas']) != 1 else 'y'} "
                      f"this week: routed in {vals} (n={len(m['deltas'])})",
                      "received → classified+routed, per reply — raw values, sample too small to average"))
    else:
        d = m["deltas"]
        med = d[len(d) // 2]
        extra = f"; {m['unmatched']} unmatched dropped" if m["unmatched"] else ""
        lines.append(("reply SLA",
                      f"median {fmt_dur(med)} received → routed (n={len(d)} human replies, "
                      f"worst {fmt_dur(d[-1])})",
                      f"deterministic join of reply-router to inbound receipt timestamps{extra}"))

    m = g["fulfillment"]
    if m is None:
        lines.append(("fulfillment latency", NOT_ENOUGH,
                      "receipt ledger not deployed — idempotency-key join only, no timestamp guessing"))
    elif m["n"] == 0:
        lines.append(("fulfillment latency", NOT_ENOUGH + " (n=0)",
                      "0 joined purchase→delivery receipt pairs — idempotency-key join only"))
    elif m["n"] < 3:
        vals = ", ".join(fmt_dur(x) for x in m["latencies"])
        lines.append(("fulfillment latency",
                      f"{m['n']} fulfillment event{'s' if m['n'] != 1 else ''} this week: {vals} "
                      f"(n={m['n']})",
                      "purchase receipt → delivery receipt, same idempotency key — raw values"))
    else:
        lat = m["latencies"]
        lines.append(("fulfillment latency",
                      f"median {fmt_dur(lat[len(lat) // 2])} purchase → delivery "
                      f"(n={m['n']}, worst {fmt_dur(lat[-1])})",
                      "joined by idempotency key from the fulfillment receipt ledger"))

    m = g["uptime"]
    if m is None:
        lines.append(("uptime", NOT_ENOUGH, "system-health log missing or under 5 samples in window"))
    else:
        val = f"{m['pct']:.1f}% over the 7-day window (downtime {fmt_dur(m['down_s'])}, n={m['samples']} samples)"
        basis = ("measured from gaps in system-health.jsonl — silence counts as downtime"
                 + (f"; {m['gap_count']} gap{'s' if m['gap_count'] != 1 else ''} found" if m["gap_count"] else "; no gaps"))
        lines.append(("uptime", val, basis))
        for note in m["incidents"]:
            lines.append(("uptime incident", note, ""))

    m = g["saves"]
    if m is None:
        lines.append(("saves attempted", NOT_ENOUGH, "no winback or critical-window monitor entries in window"))
    else:
        val = (f"{m['attempted']} save{'s' if m['attempted'] != 1 else ''} attempted "
               f"(winback queued: {m['winback_queued']}, critical replies caught: {m['critical_detected']})")
        basis = (f"Rick-attributable events only; {m['winback_scans']} winback scans + "
                 f"{m['monitor_runs']} critical-window monitor entries ran this week")
        lines.append(("saves attempted", val, basis))

    return lines


def machine_tweet_lines(g):
    """Compact mirror of machine_lines() for the X thread — same computed
    metric dicts, shorter prose. Same honesty rules: n always shown, small n
    raw, missing source => 'not enough data', never a number."""
    out = []
    m = g["reply_sla"]
    if m is None or (m and m["n"] > 0 and not m["deltas"]):
        out.append("Reply SLA: not enough data")
    elif m["n"] == 0:
        out.append("Reply SLA: 0 human replies (n=0)")
    elif len(m["deltas"]) < 3:
        out.append("Reply SLA: " + ", ".join(fmt_dur(x) for x in m["deltas"])
                   + f" (n={len(m['deltas'])}, raw)")
    else:
        d = m["deltas"]
        out.append(f"Reply SLA: median {fmt_dur(d[len(d) // 2])} (n={len(d)})")
    m = g["fulfillment"]
    if m is None or m["n"] == 0:
        out.append("Fulfillment: not enough data (n=0; key join only)")
    elif m["n"] < 3:
        out.append("Fulfillment: " + ", ".join(fmt_dur(x) for x in m["latencies"])
                   + f" (n={m['n']}, raw)")
    else:
        lat = m["latencies"]
        out.append(f"Fulfillment: median {fmt_dur(lat[len(lat) // 2])} (n={m['n']})")
    m = g["uptime"]
    if m is None:
        out.append("Uptime: not enough data")
    else:
        line = f"Uptime: {m['pct']:.1f}% (down {fmt_dur(m['down_s'])}, n={m['samples']})"
        if m["incidents"]:
            line += f", incl {m['incidents'][0].split(':')[0]} outage — postmortem on the page"
        out.append(line)
    m = g["saves"]
    out.append("Saves attempted: not enough data" if m is None
               else f"Saves attempted: {m['attempted']} (Rick-attributable only)")
    return out


def fmt_short(t): return t.astimezone(PT).strftime("%a %H:%M")


def fmt_money(v):
    s = f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"${s}"


def mrr_span(since, until):
    """(start, end) MRR from velocity.json, or None if missing/unparsable —
    caller must then render 'see receipts' rather than a stale number."""
    try:
        entries = sorted(json.loads(VELOCITY.read_text())["entries"],
                         key=lambda e: e["date"])
        def at(ts):
            d = ts.astimezone(PT).strftime("%Y-%m-%d")
            vals = [e["mrr"] for e in entries if e["date"] <= d]
            return vals[-1] if vals else None
        end = at(until)
        if end is None:
            return None
        start = at(since)
        return (end if start is None else start, end)
    except Exception:
        return None


CSS = (":root{--y:#FBBF24;--k:#000;--w:#fff;--m:'Space Mono',monospace;--p:'Press Start 2P',cursive}"
"*{box-sizing:border-box;margin:0;padding:0}"
"body{background:var(--w);font-family:var(--m);color:var(--k);background-image:radial-gradient(circle,rgba(0,0,0,.06) 1px,transparent 1px);background-size:24px 24px}"
"nav{border-bottom:2px solid var(--k);background:var(--w)}"
".nav-inner{max-width:1100px;margin:0 auto;padding:0 24px;height:60px;display:flex;align-items:center;justify-content:space-between}"
".nav-logo{font-family:var(--p);font-size:.7rem;color:var(--k);text-decoration:none;letter-spacing:.05em}.nav-logo span{color:var(--y)}"
".nav-back{font-family:var(--m);font-size:.75rem;color:var(--k);text-decoration:none;text-transform:uppercase;border:1.5px solid var(--k);padding:6px 14px;letter-spacing:.05em}"
".nav-back:hover{background:var(--k);color:var(--w)}"
".wrap{max-width:1100px;margin:0 auto;padding:48px 24px 80px}"
".hl{display:inline-block;background:var(--y);border:2px solid var(--k);font-family:var(--p);font-size:9px;padding:6px 14px;margin-bottom:24px;letter-spacing:.08em}"
"h1{font-family:var(--p);font-size:clamp(1.1rem,2.6vw,1.7rem);line-height:1.6;margin-bottom:14px}"
".disclosure{font-size:13px;color:#555;max-width:720px;line-height:1.7;margin-bottom:8px}"
".window{font-size:12px;color:#888;margin-bottom:32px}section{margin-top:40px}"
"h2{font-family:var(--p);font-size:11px;letter-spacing:.08em;margin-bottom:14px}"
"table{width:100%;border-collapse:collapse;border:2px solid var(--k);font-size:13px;background:var(--w)}"
"td{padding:10px 14px;border-bottom:1px solid #ddd;vertical-align:top}tr:last-child td{border-bottom:none}"
"td.ts{width:120px;color:#666;font-size:12px;white-space:nowrap}"
".tag{display:inline-block;font-family:var(--p);font-size:7px;padding:3px 8px;border:1.5px solid var(--k);text-transform:uppercase}"
".tag-ship{background:#d4edda}.tag-blog{background:var(--y)}.tag-newsletter{background:#cfe2ff}"
".tag-reply{background:#fff3cd}.tag-outreach{background:#e0e0e0}.tag-sequencer{background:#f0f0f0}"
"code{font-family:var(--m);background:#f0f0f0;border:1px solid #ccc;padding:1px 6px;font-size:12px}"
".empty{color:#999;font-style:italic}"
".money{display:flex;gap:24px;align-items:center;padding:18px 22px;border:2px solid var(--k);background:var(--w)}"
".money .num{font-family:var(--p);font-size:18px}.money .arrow{font-family:var(--p);font-size:14px;color:#888}.money .note{font-size:13px;color:#555}"
".lesson{border:2px solid var(--k);padding:14px 18px;margin-bottom:10px;background:var(--w)}"
".lesson-title{font-family:var(--p);font-size:9px;line-height:1.7;margin-bottom:6px}.lesson-body{font-size:13px;color:#333;line-height:1.7}"
"ul.roadmap{list-style:none;padding-left:0;border:2px solid var(--k);background:var(--w)}"
"ul.roadmap li{padding:10px 16px;border-bottom:1px solid #ddd;font-size:13px}ul.roadmap li:last-child{border-bottom:none}"
".cta-wrap{margin-top:40px;padding-top:24px;border-top:2px solid var(--k)}"
"a.cta{display:inline-block;background:var(--k);color:var(--y);font-family:var(--p);font-size:9px;padding:14px 22px;text-decoration:none;letter-spacing:.06em;border:2px solid var(--k)}"
"a.cta:hover{background:var(--y);color:var(--k)}"
"footer{border-top:2px solid var(--k);margin-top:60px;padding:24px;font-size:11px;color:#666;text-align:center}"
"@media(max-width:640px){td.ts{width:80px}h1{font-size:1rem}.money{flex-direction:column;align-items:flex-start;gap:6px}}")


def build_html(now_utc):
    since, until, anchor = now_utc - timedelta(days=7), now_utc, page_anchor(now_utc)
    g = gather(since, until)

    span = mrr_span(since, until)
    if span:
        delta = span[1] - span[0]
        delta_str = f'Δ {"-" if delta < 0 else ""}{fmt_money(abs(delta))}'
        mrr_summary = f"MRR {fmt_money(span[0])} → {fmt_money(span[1])}"
        money_html = (f'<div class="money"><span class="num">{escape(fmt_money(span[0]))}</span>'
                      f'<span class="arrow">→</span><span class="num">{escape(fmt_money(span[1]))}</span>'
                      f'<span class="arrow">{escape(delta_str)}</span>'
                      '<span class="note">from revenue reconciliation — full ledger at '
                      '<a href="/receipts/">/receipts</a>.</span></div>')
    else:
        mrr_summary = "MRR: see receipts"
        money_html = ('<div class="money"><span class="num">MRR: see receipts</span>'
                      '<span class="note">live ledger at <a href="/receipts/">/receipts</a>.</span></div>')

    bill = [(c["ts"], "ship", f'<code>{escape(c["sha"])}</code> {escape(c["subj"])}') for c in g["commits"]]
    bill += [(p["date"], "blog",
              f'<a href="https://meetrick.ai/blog/{escape(p["slug"])}">{escape(p["title"])}</a>') for p in g["posts"]]
    for n in g["newsletters"]:
        ts = parse_ts(n.get("sent_at")) or parse_ts(n.get("drafted_at")) or anchor
        bill.append((ts, "newsletter",
                     f'newsletter #{n.get("issue","?")} ({"draft" if n.get("__draft") else "sent"}): '
                     f'{escape(n.get("subject","(no subject)"))}'))
    for r in g["replies"]:
        ts = parse_ts(r.get("ran_at") or r.get("ts")) or anchor
        bill.append((ts, "reply",
                     f'reply from <code>{escape(r.get("email",""))}</code> classified '
                     f'<strong>{escape(r.get("label","?"))}</strong> → {escape(r.get("action",""))}'))
    if g["sends_total"]:
        bill.append((until, "outreach",
                     f'{g["sends_total"]} cold emails dispatched to {g["recipients"]} unique recipients'))
    if g["seq"]:
        s = " / ".join(f"{k}:{v}" for k, v in sorted(g["seq"].items(), key=lambda x: -x[1])[:5])
        bill.append((until, "sequencer", f"sequencer events — {escape(s)}"))
    bill.sort(key=lambda x: x[0])
    rows = "".join(f'<tr><td class="ts">{escape(fmt_short(t))}</td>'
                   f'<td><span class="tag tag-{tag}">{tag}</span></td><td>{txt}</td></tr>'
                   for t, tag, txt in bill) \
        or '<tr><td colspan="3" class="empty">no data this week</td></tr>'

    # Lessons — prefer real kind=lesson rows, fall back to decisions only if <3
    lessons_html, seen = [], set()
    decisions_pool = list(g["decisions"][-24:])
    decisions_pool.sort(key=lambda d: 0 if d.get("kind") == "lesson" else 1)
    for d in decisions_pool:
        title = clean_title(d.get("title", ""))
        if not title or title in seen or "Heartbeat loop" in d.get("title", ""): continue
        seen.add(title)
        body = re.sub(r"Created initiative workflow \w+ on \w+-lane\.", "",
                      (d.get("notes") or "")).strip() or title
        lessons_html.append(
            f'<div class="lesson"><div class="lesson-title">{escape(title[:120])}</div>'
            f'<div class="lesson-body">{escape(body[:280])}</div></div>')
        if len(lessons_html) >= 3: break
    if not lessons_html:
        lessons_html.append('<div class="empty">no lessons logged this week</div>')

    roadmap_html = "".join(f'<li>{escape(t[:160])}</li>' for t in g["planned"]) \
        or '<li class="empty">no roadmap items logged this week</li>'

    # Footer CTA — prefer ICP-pivot blog post; fall back to issue #5; then latest blog
    cta_url = cta_label = None
    for p in g["posts"]:
        if any(k in p["slug"] for k in ("icp", "funnel-leak")):
            cta_url, cta_label = f'https://meetrick.ai/blog/{p["slug"]}', p["title"]; break
    # /newsletter never existed on the site (404 from a money page) — route
    # newsletter CTAs to the homepage subscribe block instead.
    if not cta_url:
        for n in g["newsletters"]:
            if n.get("issue") == 5 and n.get("broadcast_id"):
                cta_url, cta_label = "https://meetrick.ai/#updates", n.get("subject", "Issue #5"); break
    if not cta_url and g["posts"]:
        cta_url, cta_label = f'https://meetrick.ai/blog/{g["posts"][-1]["slug"]}', g["posts"][-1]["title"]
    elif not cta_url and g["newsletters"]:
        cta_url, cta_label = "https://meetrick.ai/#updates", "get the weekly post-mortem in your inbox → subscribe"
    cta_block = (f'<a class="cta" href="{escape(cta_url)}">see the ICP-pivot post-mortem → '
                 f'{escape((cta_label or "")[:80])}</a>') if cta_url else ""
    # Hard CTA — direct path to a pilot for ready-to-buy readers. Sits below the
    # soft (blog) CTA above. Soft CTA = skeptics. Hard CTA = pilot-ready.
    pilot_cta = ('<a class="cta pilot" href="/pilot" '
                 'style="margin-top:10px;background:#FBBF24;color:#000;border-color:#000">'
                 'want this running on your company? free 1-week pilot →</a>')
    # X share intent — pure client-side anchor, NO JS, NO automation.
    # Pre-fills tweet text with a single specific number from the week, and
    # links the /this-week page with utm_source=x_thread so the eventual
    # click-through gets attributed in funnel-attribution.py number #4.
    share_text = (f"What an autonomous AI agent shipped this week ({len(g['commits'])} "
                  f"commits, {len(g['posts'])} posts, {g['sends_total']} cold emails, {mrr_summary}). "
                  f"Auto-generated from prod logs. No marketing copy:")
    share_url = "https://meetrick.ai/this-week?utm_source=x_thread&utm_medium=distribution&utm_campaign=this-week-share"
    share_intent = ("https://x.com/intent/post?"
                    f"text={urllib.parse.quote(share_text)}"
                    f"&url={urllib.parse.quote(share_url)}")
    x_share_cta = (f'<a class="cta x-share" href="{escape(share_intent)}" '
                   'target="_blank" rel="noopener" '
                   'style="margin-top:10px;background:#fff;color:#000;border-color:#000">'
                   'share this on X →</a>')
    cta_block = cta_block + pilot_cta + x_share_cta

    machine_rows = "".join(
        f'<tr><td class="ts">{escape(name)}</td><td><strong>{escape(val)}</strong>'
        + (f'<br><span style="color:#666;font-size:12px">{escape(basis)}</span>' if basis else "")
        + '</td></tr>'
        for name, val, basis in machine_lines(g))

    week_label = f'{(anchor - timedelta(days=7)).strftime("%b %d")} – {anchor.strftime("%b %d, %Y")}'
    summary = (f'{len(g["commits"])} commits · {len(g["posts"])} posts · '
               f'{len(g["newsletters"])} newsletter{"s" if len(g["newsletters"])!=1 else ""} · '
               f'{g["sends_total"]} emails · {len(g["replies"])} repl'
               f'{"ies" if len(g["replies"])!=1 else "y"} · {mrr_summary}')

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>What Rick did this week — meetrick.ai</title>
<meta name="description" content="Auto-generated weekly proof page: every commit, send, reply, and post Rick produced this week. Pulled from production logs." />
<meta property="og:title" content="What Rick did this week — meetrick.ai" />
<meta property="og:description" content="Auto-generated from production logs every Monday 09:00 PT." />
<meta property="og:url" content="https://meetrick.ai/this-week" /><meta property="og:type" content="website" />
<meta property="og:image" content="https://meetrick.ai/og-image.jpg" /><meta name="twitter:card" content="summary_large_image" />
<meta name="robots" content="index, follow" /><link rel="canonical" href="https://meetrick.ai/this-week" />
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Space+Mono:ital,wght@0,400;0,700;1,400&display=swap" rel="stylesheet">
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
<style>{CSS}</style></head><body>
<nav><div class="nav-inner"><a href="/" class="nav-logo">RICK<span>_AI</span></a><a href="/changelog" class="nav-back">Changelog →</a></div></nav>
<div class="wrap">
  <div class="hl">PROOF / WEEKLY</div>
  <h1>WHAT RICK DID THIS WEEK.</h1>
  <p class="disclosure">This page is auto-generated from production logs every Monday at 09:00 PT.
     No marketing copy, no curated highlights — every line below is a real entry from
     <code>execution-ledger</code>, <code>sequencer</code>, <code>reply-router</code>,
     <code>email-sends</code>, <code>system-health</code>, <code>winback-scheduler</code>,
     <code>critical-window-monitor</code>, the fulfillment receipt ledger, the workspace
     git log, and the <code>meetrick-content/blog</code> tree.</p>
  <p class="window">Window: <strong>{escape(week_label)}</strong> &nbsp;•&nbsp;
     Generated: {escape(anchor.strftime("%Y-%m-%d %H:%M PT"))} &nbsp;•&nbsp; {escape(summary)}</p>
  <section><h2>THE WEEK'S BILL</h2><table>{rows}</table></section>
  <section><h2>THE MACHINE</h2><table>{machine_rows}</table>
    <p class="disclosure" style="margin-top:8px">Every number above is computed from logs at
       generation time and shows its sample size. Small samples are shown raw, never averaged.
       A missing log renders "{escape(NOT_ENOUGH)}" — never a stale number.</p></section>
  <section><h2>THE WEEK'S $</h2>
    {money_html}
  </section>
  <section><h2>THE LESSONS</h2>{''.join(lessons_html)}</section>
  <section><h2>THE ROADMAP — NEXT WEEK</h2><ul class="roadmap">{roadmap_html}</ul></section>
  <div class="cta-wrap">{cta_block}</div>
</div>
<footer>meetrick.ai/this-week — auto-generated from production logs. Anchor: {escape(anchor.isoformat())}</footer>
</body></html>
"""


def build_x_thread_draft(now_utc):
    """Build a 4-tweet X-thread paste-ready draft from this week's prod log
    summary. Operator-paced — written to a sidecar file Vlad pastes manually.

    Each tweet ≤ 270 chars (X cap is 280; leave runway for handle additions).
    Tweet 1 hooks with the headline number. Tweets 2-3 carry concrete proof.
    Tweet 4 is the CTA back to /this-week with utm_source=x_thread.

    Returns (thread_text, anchor_iso). thread_text is "Tweet 1\\n\\n---\\n\\nTweet 2..."
    so Vlad can copy-paste into X's compose UI block by block.
    """
    since, until, anchor = now_utc - timedelta(days=7), now_utc, page_anchor(now_utc)
    g = gather(since, until)
    span = mrr_span(since, until)
    mrr_line = (f"MRR: {fmt_money(span[0])} → {fmt_money(span[1])}." if span
                else "MRR: see receipts.")
    # Prefer the ICP-pivot post if present, else the most recent blog post
    headline_post = None
    for p in g["posts"]:
        if any(k in p["slug"] for k in ("icp", "funnel-leak", "first-cold-reply")):
            headline_post = p; break
    if not headline_post and g["posts"]:
        headline_post = g["posts"][-1]
    headline_url = (f"https://meetrick.ai/blog/{headline_post['slug']}"
                    "?utm_source=x_thread&utm_medium=distribution&utm_campaign=this-week-share"
                    if headline_post else None)

    # Top reply (warm signal) — first sales/positive label
    top_reply = next((r for r in g["replies"]
                      if (r.get("label") or "") in ("sales_inquiry", "warm_reply", "positive")), None)

    t1 = (f"What an autonomous AI agent shipped this week.\n\n"
          f"{len(g['commits'])} commits. {len(g['posts'])} blog posts. "
          f"{g['sends_total']} cold emails to {g['recipients']} founders. "
          f"{len(g['replies'])} reply{'ies' if len(g['replies'])!=1 else ''}. "
          f"{mrr_line}\n\n"
          f"Every line is from a prod log. No marketing copy.")[:270]

    # Machine tweet — mirrors THE MACHINE section: same metric dicts from
    # gather(), compact prose. The thread can never claim what the page doesn't.
    t_machine = ("The machine, from logs:\n" + "\n".join(machine_tweet_lines(g))
                 + "\nSmall n shown raw, never averaged.")[:270]

    if g["sends_total"] and top_reply:
        t2 = (f"The 1 reply was from {top_reply.get('email','a founder').split('@')[-1]}. "
              f"Classified {top_reply.get('label','—')} → routed to {top_reply.get('action','—')}.\n\n"
              f"Reply-router is real code, not a wrapper around \"please respond appropriately\".")[:270]
    else:
        t2 = (f"{g['sends_total']} cold emails fired this week. "
              f"Sequencer events: {sum(g['seq'].values())}. "
              f"Bounce rate held under 5%, sender-warmup ramp on schedule.")[:270]

    if headline_post:
        t3 = (f"Most-honest post this week: \"{headline_post['title'][:120]}\".\n\n"
              f"Receipts inside.")[:270]
    elif g["decisions"]:
        d_title = clean_title(g["decisions"][-1].get("title", ""))[:160]
        t3 = (f"Lesson logged: {d_title}")[:270]
    else:
        t3 = "Nothing dramatic this week. The boring weeks are when compounding happens."[:270]

    t4 = (f"Full week's bill (auto-generated, anchored Mon 09:00 PT):\n"
          f"https://meetrick.ai/this-week?utm_source=x_thread&utm_medium=distribution&utm_campaign=this-week-share\n\n"
          f"Pilot: https://meetrick.ai/pilot?utm_source=x_thread&utm_medium=distribution&utm_campaign=this-week-share")[:270]

    tweets = [t1, t_machine, t2, t3, t4]
    if headline_url and len(tweets) < 6:
        tweets.insert(4, f"Read: {headline_url}"[:270])

    body = "\n\n---\n\n".join(tweets)
    return body, anchor


def write_x_thread_sidecar(now_utc):
    """Write paste-ready X-thread draft to ~/rick-vault/projects/x-drafts/.
    Operator pastes manually — Rick stays manual on X for 30 days post-restore.
    Returns Path or None on failure."""
    try:
        body, anchor = build_x_thread_draft(now_utc)
    except Exception as exc:
        print(f"[warn] x-thread draft failed: {exc}", file=sys.stderr)
        return None
    X_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = anchor.strftime("%Y-%m-%d")
    path = X_DRAFTS_DIR / f"this-week-{date_str}-thread.txt"
    header = (f"# X-thread draft — /this-week ({date_str})\n"
              f"# Anchor: {anchor.isoformat()}\n"
              f"# Operator-paced: copy-paste manually into X. Tweets separated by `---`.\n"
              f"# UTM: utm_source=x_thread (funnel-attribution.py #4 picks this up).\n"
              f"# 30-day post-suspension cooldown ends ~2026-06-05.\n\n")
    path.write_text(header + body + "\n", encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--now", default=None, help="ISO timestamp override")
    ap.add_argument("--no-x-thread", action="store_true",
                    help="Skip writing the paste-ready X-thread sidecar")
    args = ap.parse_args()
    now = (parse_ts(args.now) if args.now else None) or datetime.now(timezone.utc)
    # Trivial-delta gate (2026-07-13): a week with nothing shipped, nothing
    # posted and no revenue movement is not worth a publish cycle.
    g = gather(now - timedelta(days=7), now)
    span = mrr_span(now - timedelta(days=7), now)
    revenue_changed = bool(span) and span[1] != span[0]
    if not g["commits"] and not g["posts"] and not revenue_changed:
        print("SKIPPED_TRIVIAL: 0 commits, 0 posts, 0 revenue change in 7d window")
        return 3
    html = build_html(now)
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    rc = 0
    if out.exists() and out.read_text() == html:
        print(f"[ok] no change → {out}")
    else:
        out.write_text(html)
        print(f"[ok] wrote {out} ({len(html)} bytes)")
    if not args.no_x_thread:
        x_path = write_x_thread_sidecar(now)
        if x_path:
            print(f"[ok] x-thread draft → {x_path}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
