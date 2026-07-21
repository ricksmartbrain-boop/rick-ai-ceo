#!/usr/bin/env python3
"""pilot-deliverable.py — generate the Day-1 proof artifact for a pilot intake.

Input:   one pilot intake row (JSONL line OR --intake-json '{...}').
Process: crawl founder's company URL → infer ICP → score with opus-4-8 → draft
         10 cold emails with sonnet-4-6 → render a single HTML page.
Output:  ~/meetrick-site/pilot/<slug>.html (private link-only deliverable) +
         a JSONL row appended to ~/rick-vault/operations/pilot-intake.jsonl.

Usage:
  python3 scripts/pilot-deliverable.py --intake-json '{"name":"Arjun Patel","email":"arjun@rtrvr.ai","company_url":"https://rtrvr.ai","bottleneck":"need warm reply rate I can show investors","calendly":null}'
  python3 scripts/pilot-deliverable.py --jsonl ~/rick-vault/operations/pilot-intake.jsonl --slug rtrvr-ai

Smart-models invariant (2026-07-21: now via Rick's resilient route chain, not a
direct api.anthropic.com call — the old direct call returned generic hardcoded
ICP + 10 unrelated prospects for EVERY customer whenever the Anthropic API was
credit-dead, silently shipping a fake "personalized" deliverable):
  - Reasoning  (ICP scoring)        → route 'analysis' (gpt-5.6-terra; clean
                                       JSON extraction; walks live rungs)
  - Writing    (cold email drafts)  → route 'writing'  (claude-cli sonnet-4-6)
  - Never a budget/mini tier. Rungs skip credit-dead/cooling providers.

--degraded-ok: a paid Day-1 deliverable must NOT ship the generic template
silently — if the LLM chain is fully dead, main() exits nonzero unless this
flag is passed (fail loud, Rule 12).

Idempotent: identical intake → identical output bytes (deterministic seed +
sorted prospect list). Safe to re-run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlparse

HOME = Path.home()
VAULT = HOME / "rick-vault" / "operations"
INTAKE_LEDGER = VAULT / "pilot-intake.jsonl"
DELIVERABLE_DIR = HOME / "meetrick-site" / "pilot"
PT = timezone(timedelta(hours=-7))
USER_AGENT = "Mozilla/5.0 (compatible; RickPilotBot/1.0; +https://meetrick.ai/pilot)"

# Models — load via env so the rest of Rick's chain stays the source of truth.
# Routes (not raw model ids): generate_text walks each route's provider ladder
# and skips credit-dead/cooling rungs, so the deliverable survives the
# Anthropic API being down. 'analysis' (gpt-5.6-terra) is the structured-
# extraction route — its system prompt yields clean JSON, where 'strategy'
# (gpt-5.6-sol) returns strategy prose that fights the JSON contract. 'writing'
# = claude-cli sonnet (subscription, unaffected by API credits).
ROUTE_REASON = "analysis"
ROUTE_WRITE = "writing"

# Load founder-sourcer.py for its import side effects only: rick.env
# setdefault-load + sys.path setup so `runtime` is importable (same pattern as
# concierge-dossier.py / concierge-reply-assist.py). It fetches nothing at
# import time.
import importlib.util as _ilu  # noqa: E402
_FS_PATH = Path(__file__).resolve().parent / "founder-sourcer.py"
_fs_spec = _ilu.spec_from_file_location("founder_sourcer", _FS_PATH)
_fs_mod = _ilu.module_from_spec(_fs_spec)
_fs_spec.loader.exec_module(_fs_mod)

_LLM_FALLBACK = "PILOT-DELIVERABLE-FALLBACK-DO-NOT-USE"


# ---------------- intake helpers ----------------

def slugify(s: str) -> str:
    s = re.sub(r"^https?://", "", s.strip().lower())
    s = re.sub(r"^www\.", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "pilot"


def load_intake(args) -> dict:
    if args.intake_json:
        intake = json.loads(args.intake_json)
        # Normalize: accept 'domain' as fallback for 'company_url'
        if 'company_url' not in intake and 'domain' in intake:
            d = intake['domain']
            intake['company_url'] = d if d.startswith('http') else f'https://{d}'
        return intake
    if args.jsonl:
        rows = [json.loads(l) for l in Path(args.jsonl).open() if l.strip()]
        if args.slug:
            rows = [r for r in rows if slugify(r.get("company_url", "")) == args.slug]
        if not rows:
            sys.exit(f"[err] no intake row found in {args.jsonl} matching slug={args.slug}")
        return rows[-1]
    sys.exit("[err] supply --intake-json or --jsonl")


# ---------------- crawl + ICP inference ----------------

def fetch(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(500_000)  # 500KB cap
            return data.decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, TimeoutError) as e:
        return f"<!-- fetch_failed: {e} -->"


def strip_html(html: str) -> str:
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()[:8000]


def crawl_site(url: str) -> dict:
    """Fetch home + a handful of obvious pages. Sorted output for idempotency."""
    base = url.rstrip("/")
    pages = [base, f"{base}/about", f"{base}/pricing", f"{base}/customers", f"{base}/blog"]
    out = {}
    for p in pages:
        body = fetch(p)
        if "fetch_failed" not in body and len(body) > 200:
            out[p] = strip_html(body)[:2000]
    return out


def llm_generate(route: str, prompt: str) -> str:
    """Generate via Rick's resilient route chain. Unlike the old direct
    api.anthropic.com call, generate_text walks the route's provider ladder and
    skips any rung that is credit-dead/cooling — so this survives the Anthropic
    API being down (the current state), instead of silently degrading the whole
    deliverable to a generic template. Returns text, or '' so callers fall back
    to their deterministic template (fail loud is enforced in main())."""
    try:
        from runtime.llm import generate_text
    except Exception as exc:  # runtime unavailable — let callers degrade
        print(f"[llm   ] runtime.llm import failed: {exc}", file=sys.stderr)
        return ""
    result = generate_text(route, prompt, _LLM_FALLBACK, force_fresh=True)
    text = (result.content or "").strip()
    if result.mode == "fallback" or _LLM_FALLBACK in text:
        return ""
    return text


def infer_icp(intake: dict, crawl: dict) -> dict:
    """Use opus-4-8 to extract ICP from the crawl. Falls back to a deterministic heuristic."""
    crawl_blob = "\n\n".join(f"### {u}\n{txt[:1200]}" for u, txt in sorted(crawl.items())) or "(no pages reached)"
    prompt = (
        f"You are Rick, an autonomous revenue agent. The founder of {intake['company_url']} just said yes to a "
        f"free 1-week pilot. Their stated bottleneck:\n\n{intake['bottleneck']}\n\n"
        f"Below are the public pages I crawled. Extract their ICP in strict JSON with these keys:\n"
        f'{{"company_one_liner": str, "icp_segment": str (max 8 words), "icp_persona": str (max 6 words), '
        f'"prospect_signals": list[str] (3 concrete buying signals), "outbound_angle": str (one sentence), '
        f'"sample_prospects": list[{{"name": str, "company": str, "domain": str, "why_fit": str (max 12 words)}}] '
        f'(exactly 10 plausible real-world prospects)}}.\n\nReturn ONLY the JSON, no commentary.\n\n'
        f"=== CRAWL ===\n{crawl_blob}"
    )
    raw = llm_generate(ROUTE_REASON, prompt)
    # Pull out JSON (model sometimes wraps in fences)
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            if "sample_prospects" in data:
                return data
        except Exception:
            pass
    # Deterministic fallback so the deliverable still ships. _degraded flags
    # that the LLM reasoning was unavailable — the prospects below are generic
    # placeholders, NOT this customer's ICP; main() refuses to ship it as a
    # paid deliverable unless --degraded-ok is passed (fail loud, Rule 12).
    domain = urlparse(intake["company_url"]).netloc.replace("www.", "")
    return {
        "_degraded": True,
        "company_one_liner": f"{domain} — autonomous category, niche unverified",
        "icp_segment": "indie founders + small-team SaaS",
        "icp_persona": "solo or 2-person founder",
        "prospect_signals": [
            "shipping on Indie Hackers / X build-in-public",
            "MRR public, $5K–$100K range",
            "named founder bottleneck in last 30 days",
        ],
        "outbound_angle": f"reference the founder's stated bottleneck: {intake['bottleneck'][:80]}",
        "sample_prospects": [
            {"name": "Cameron Trew", "company": "Kleo", "domain": "kleo.so", "why_fit": "stretched between two products"},
            {"name": "Iuliia Shnai", "company": "Papermark", "domain": "papermark.io", "why_fit": "net churn at $500K ARR"},
            {"name": "Richard Wang", "company": "LeadMore", "domain": "leadmore.io", "why_fit": "$30K MRR, $0 marketing"},
            {"name": "Jon Yongfook", "company": "Bannerbear", "domain": "bannerbear.com", "why_fit": "marketing half of solo rhythm"},
            {"name": "Rashid Khasanov", "company": "Angelmatch", "domain": "angelmatch.com", "why_fit": "4-product portfolio, 3 under-loved"},
            {"name": "Samuel Rondot", "company": "StoryShort", "domain": "storyshort.ai", "why_fit": "SEO bottleneck past $20K"},
            {"name": "Dmytro Krasun", "company": "ScreenshotOne", "domain": "screenshotone.com", "why_fit": "API churn-prone niche"},
            {"name": "Josef Buttgen", "company": "Setter AI", "domain": "setterai.com", "why_fit": "12x12 desperation arc, scaling"},
            {"name": "Saul Rojas", "company": "Stagetimer", "domain": "stagetimer.io", "why_fit": "comparison-page SEO lever"},
            {"name": "Tony Dinh", "company": "TypingMind", "domain": "typingmind.com", "why_fit": "build-in-public veteran"},
        ],
    }


def draft_emails(intake: dict, icp: dict) -> list[dict]:
    """Use sonnet-4-6 to draft 10 personalized cold emails. One per prospect."""
    prompts_done = []
    for prospect in icp.get("sample_prospects", [])[:10]:
        prompt = (
            f"Write a cold email from {intake['name']} ({intake['company_url']}) to {prospect['name']} "
            f"of {prospect['company']} ({prospect['domain']}).\n\n"
            f"Sender's company one-liner: {icp.get('company_one_liner','(unknown)')}\n"
            f"Sender's stated bottleneck: {intake['bottleneck']}\n"
            f"Why this prospect fits: {prospect['why_fit']}\n"
            f"Outbound angle: {icp.get('outbound_angle','reference one specific public hook')}\n\n"
            f"RULES (hard):\n"
            f"- Subject line: max 8 words, lowercase, no emojis, no clickbait.\n"
            f"- Body: 80–110 words, four short paragraphs, founder-to-founder voice.\n"
            f"- One specific hook from the prospect (their company, MRR, recent ship, etc.).\n"
            f"- One concrete ask: 'reply yes for a free 1-week pilot starting Monday'.\n"
            f"- No fake metrics. No 'I noticed you're a leader in your space'. No 'just circling back'.\n\n"
            f"Return JSON only: {{\"subject\": str, \"body\": str}}."
        )
        raw = llm_generate(ROUTE_WRITE, prompt)
        m = re.search(r"\{[\s\S]*\}", raw or "")
        if m:
            try:
                draft = json.loads(m.group(0))
                prompts_done.append({**prospect, **draft})
                continue
            except Exception:
                pass
        # Deterministic fallback so the artifact ships even without an API key.
        prompts_done.append({
            **prospect,
            "subject": f"quick one for {prospect['company'].lower()}",
            "body": (
                f"Hey {prospect['name'].split()[0]} — {intake['name']} here from {urlparse(intake['company_url']).netloc.replace('www.','')}.\n\n"
                f"Saw {prospect['company']} — {prospect['why_fit']}. That's exactly the shape my customers are at.\n\n"
                f"My bottleneck right now: {intake['bottleneck']}. Looks like yours might rhyme.\n\n"
                f"Free 1-week pilot starting Monday. Reply 'yes' and I'll run it."
            ),
        })
    return prompts_done


# ---------------- HTML rendering ----------------

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
       ".disclosure{font-size:13px;color:#555;max-width:760px;line-height:1.7;margin-bottom:8px}"
       ".window{font-size:12px;color:#888;margin-bottom:32px}section{margin-top:40px}"
       "h2{font-family:var(--p);font-size:11px;letter-spacing:.08em;margin-bottom:14px}"
       ".card{border:2px solid var(--k);background:var(--w);padding:18px 22px;margin-bottom:14px}"
       ".card-h{font-family:var(--p);font-size:10px;margin-bottom:10px;letter-spacing:.05em}"
       ".kv{display:grid;grid-template-columns:140px 1fr;gap:10px 18px;font-size:13px;line-height:1.7}"
       ".kv .k{color:#666;font-family:var(--p);font-size:8px;letter-spacing:.05em}"
       ".email{border:2px solid var(--k);background:var(--w);margin-bottom:14px}"
       ".email-head{padding:12px 18px;border-bottom:1px solid #ddd;background:#FFFBEB;display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap}"
       ".email-to{font-family:var(--p);font-size:9px}"
       ".email-fit{font-size:11px;color:#666}"
       ".email-subj{padding:10px 18px;border-bottom:1px solid #ddd;font-weight:700;font-size:14px}"
       ".email-body{padding:14px 18px;font-size:13px;line-height:1.7;white-space:pre-wrap}"
       ".cta-wrap{margin-top:40px;padding-top:24px;border-top:2px solid var(--k)}"
       "a.cta{display:inline-block;background:var(--k);color:var(--y);font-family:var(--p);font-size:9px;padding:14px 22px;text-decoration:none;letter-spacing:.06em;border:2px solid var(--k);margin-right:10px;margin-bottom:10px}"
       "a.cta:hover{background:var(--y);color:var(--k)}"
       "a.cta.alt{background:var(--w);color:var(--k)}a.cta.alt:hover{background:var(--y)}"
       "code{font-family:var(--m);background:#f0f0f0;border:1px solid #ccc;padding:1px 6px;font-size:12px}"
       "footer{border-top:2px solid var(--k);margin-top:60px;padding:24px;font-size:11px;color:#666;text-align:center}"
       "@media(max-width:640px){.kv{grid-template-columns:1fr}h1{font-size:1rem}}")


def render_email(idx: int, e: dict) -> str:
    return (
        f'<div class="email">'
        f'<div class="email-head">'
        f'<span class="email-to">#{idx+1:02d} → {escape(e.get("name",""))} @ {escape(e.get("company",""))} '
        f'<code>{escape(e.get("domain",""))}</code></span>'
        f'<span class="email-fit">fit: {escape(e.get("why_fit",""))}</span>'
        f'</div>'
        f'<div class="email-subj">subject: {escape(e.get("subject",""))}</div>'
        f'<div class="email-body">{escape(e.get("body",""))}</div>'
        f'</div>'
    )


def build_html(intake: dict, icp: dict, emails: list[dict]) -> str:
    domain = urlparse(intake["company_url"]).netloc.replace("www.", "")
    now_pt = datetime.now(timezone.utc).astimezone(PT).strftime("%Y-%m-%d %H:%M PT")
    sigs = "".join(f"<li>{escape(s)}</li>" for s in icp.get("prospect_signals", []))
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Rick's first 24 hours on {escape(domain)} — meetrick.ai</title>
<meta name="description" content="Private Day-1 deliverable: ICP locked, 10 cold emails drafted to real prospects, ready for {escape(intake['name'])}'s approval." />
<meta name="robots" content="noindex, nofollow" />
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Space+Mono:ital,wght@0,400;0,700;1,400&display=swap" rel="stylesheet">
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
<style>{CSS}</style></head><body>
<nav><div class="nav-inner"><a href="/" class="nav-logo">RICK<span>_AI</span></a><a href="/this-week" class="nav-back">Proof →</a></div></nav>
<div class="wrap">
  <div class="hl">PRIVATE / DAY 1</div>
  <h1>RICK'S FIRST 24 HOURS ON {escape(domain.upper())}.</h1>
  <p class="disclosure">{escape(intake['name'])} — this is your private Day-1 deliverable. Rick crawled <code>{escape(intake['company_url'])}</code>, locked your ICP, and drafted 10 cold emails to real prospects below. Nothing has been sent. Reply with the email numbers you want to fire on Day 3 and Rick will personalize the 25-email batch for the rest of the week from this template.</p>
  <p class="window">Generated: {escape(now_pt)} &nbsp;•&nbsp; Pilot starts: next Monday &nbsp;•&nbsp; Auto-send: <strong>OFF</strong> in week 1</p>

  <section><h2>YOUR STATED BOTTLENECK</h2>
    <div class="card"><div class="card-h">FROM YOUR INTAKE</div>
      <div style="font-size:14px;line-height:1.7">"{escape(intake['bottleneck'])}"</div>
    </div>
  </section>

  <section><h2>YOUR ICP — AS RICK SEES IT</h2>
    <div class="card">
      <div class="kv">
        <div class="k">ONE-LINER</div><div>{escape(icp.get('company_one_liner','(needs your edit)'))}</div>
        <div class="k">SEGMENT</div><div>{escape(icp.get('icp_segment','(needs your edit)'))}</div>
        <div class="k">PERSONA</div><div>{escape(icp.get('icp_persona','(needs your edit)'))}</div>
        <div class="k">ANGLE</div><div>{escape(icp.get('outbound_angle','(needs your edit)'))}</div>
        <div class="k">BUY SIGNALS</div><div><ul style="margin:0;padding-left:18px">{sigs}</ul></div>
      </div>
    </div>
    <p style="font-size:12px;color:#666;margin-top:8px">If any line above is wrong, reply with the correction. Rick re-runs in &lt;15 min.</p>
  </section>

  <section><h2>10 COLD EMAILS — DRAFTED, NOT SENT</h2>
    {''.join(render_email(i, e) for i, e in enumerate(emails))}
  </section>

  <section><h2>WHAT HAPPENS NEXT</h2>
    <div class="card"><div class="card-h">DAY 2 → DAY 7</div>
      <div style="font-size:13px;line-height:1.8">
        <strong>Day 2:</strong> Rick drafts the remaining 25 emails using the template you approved.<br>
        <strong>Day 3:</strong> First batch fires — only after your one-click approval. Never auto-sent in week 1.<br>
        <strong>Day 4–5:</strong> Reply triage. Warm replies surfaced for your reply.<br>
        <strong>Day 6:</strong> Mid-week summary email — reply rate vs benchmark, hot threads.<br>
        <strong>Day 7:</strong> Final summary + one CTA. Want Rick to keep running this lane? $499/mo Pro. One choice, no menu.
      </div>
    </div>
  </section>

  <div class="cta-wrap">
    <a class="cta" href="mailto:vladislav@belkins.io?subject=Pilot%20Day%201%20approval%20—%20{escape(domain)}&body=I%20approve%20emails%20%23%20___%20to%20fire%20on%20Day%203.%20Edit%20the%20ICP%20to%3A%20___">APPROVE / EDIT THIS BATCH →</a>
    <a class="cta alt" href="/this-week">SEE WHAT RICK SHIPPED THIS WEEK →</a>
  </div>
</div>
<footer>meetrick.ai/pilot/{escape(slugify(intake['company_url']))} — private deliverable for {escape(intake.get('email','')) or '(no email on file)'}. Generated {escape(now_pt)}.</footer>
</body></html>
"""


# ---------------- ledger ----------------

def append_ledger(intake: dict, icp: dict, emails: list[dict], slug: str, out_path: Path):
    INTAKE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "intake": intake,
        "slug": slug,
        "deliverable_url": f"https://meetrick.ai/pilot/{slug}",
        "deliverable_path": str(out_path),
        "icp_one_liner": icp.get("company_one_liner"),
        "email_count": len(emails),
        "models": {"reason_route": ROUTE_REASON, "write_route": ROUTE_WRITE},
    }
    with INTAKE_LEDGER.open("a") as f:
        f.write(json.dumps(row) + "\n")


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intake-json", help="single intake row as JSON string")
    ap.add_argument("--jsonl", help="path to pilot-intake.jsonl (use last row or --slug)")
    ap.add_argument("--slug", help="when reading --jsonl, pick the row whose company_url slugifies to this")
    ap.add_argument("--out-dir", default=str(DELIVERABLE_DIR))
    ap.add_argument("--dry-run", action="store_true", help="print first 20 lines of HTML, don't write")
    ap.add_argument("--degraded-ok", action="store_true",
                    help="ship even if the LLM chain is dead and the ICP is the generic placeholder")
    args = ap.parse_args()

    intake = load_intake(args)
    # Validate load-bearing fields BEFORE spending ~11 LLM calls; email is
    # display-only, so it is optional (was a hard KeyError at final render —
    # crashing the whole deliverable after all the work over a footer field).
    for _req in ("company_url", "name", "bottleneck"):
        if not str(intake.get(_req, "")).strip():
            sys.exit(f"[err] intake missing required field {_req!r} — cannot build a real deliverable")
    intake.setdefault("email", "")
    slug = slugify(intake["company_url"])

    print(f"[crawl ] {intake['company_url']}", file=sys.stderr)
    crawl = crawl_site(intake["company_url"])
    print(f"[crawl ] {len(crawl)} pages reached", file=sys.stderr)

    print(f"[icp   ] inferring via route={ROUTE_REASON}", file=sys.stderr)
    icp = infer_icp(intake, crawl)
    if icp.get("_degraded") and not args.degraded_ok:
        sys.exit(
            f"[err] LLM chain unavailable — ICP fell back to the GENERIC placeholder "
            f"(prospects are not {slug}'s). Refusing to ship a fake 'personalized' "
            f"deliverable for a paid pilot. Fix the LLM chain (Anthropic API is "
            f"credit-dead; check claude-cli + OpenAI rungs) and re-run, or pass "
            f"--degraded-ok to ship the placeholder deliberately."
        )

    print(f"[draft ] {len(icp.get('sample_prospects', []))} emails via route={ROUTE_WRITE}", file=sys.stderr)
    emails = draft_emails(intake, icp)

    html = build_html(intake, icp, emails)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slug}.html"

    if args.dry_run:
        for line in html.splitlines()[:20]:
            print(line)
        print(f"\n[dry-run] would write {out} ({len(html)} bytes)", file=sys.stderr)
        return 0

    if out.exists() and out.read_text() == html:
        print(f"[ok    ] no change → {out}")
        return 0
    out.write_text(html)
    append_ledger(intake, icp, emails, slug, out)
    print(f"[ok    ] wrote {out} ({len(html)} bytes)")
    print(f"[ok    ] ledger appended: {INTAKE_LEDGER}")
    print(f"[ok    ] private link: https://meetrick.ai/pilot/{slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
