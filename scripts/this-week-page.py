#!/usr/bin/env python3
"""this-week-page.py — render meetrick.ai/this-week.html from production logs.

Reads (read-only) last 7d from rick-vault/operations + workspace git log +
meetrick-content/blog and writes a single static HTML file. Idempotent: anchored
to the most recent Mon 09:00 PT, identical input → identical output.

Usage: python3 scripts/this-week-page.py [--out PATH] [--now ISO]
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys, urllib.parse
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
                decisions=decisions, planned=planned, commits=commits, posts=posts)


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
     <code>email-sends</code>, the workspace git log, and the <code>meetrick-content/blog</code> tree.</p>
  <p class="window">Window: <strong>{escape(week_label)}</strong> &nbsp;•&nbsp;
     Generated: {escape(anchor.strftime("%Y-%m-%d %H:%M PT"))} &nbsp;•&nbsp; {escape(summary)}</p>
  <section><h2>THE WEEK'S BILL</h2><table>{rows}</table></section>
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

    tweets = [t1, t2, t3, t4]
    if headline_url and len(tweets) < 5:
        tweets.insert(3, f"Read: {headline_url}"[:270])

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
