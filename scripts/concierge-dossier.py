#!/usr/bin/env python3
"""
concierge-dossier.py — one-shot dossier + HN-card generator (D3 + D2 Phase-1,
spec: rick-vault/operations/demand-systems-design-2026-07-19.md).

ALL LLM work for the concierge console lives HERE, so hallucination can never
touch the deterministic console generator (concierge-console.py stays no-LLM)
or the ledger (CHECKLIST.md — this script never writes it).

Part 1 — dossiers. For each send-eligible NN-*.md draft in
~/rick-vault/go-to-market/concierge-batch-2026-07-14/ (skip 20-* — synthetic
DO-NOT-SEND persona; 21-nada-tunelab.md is its replacement), re-fetch the
prospect site via founder-sourcer.py's fetch helpers (the sourcer persists no
site facts — re-fetch is required), summarize via runtime.llm route='analysis',
and write NN-dossier.md beside the draft. Dossier scope, HARD-CAPPED:
  - 3 site facts, EACH with a VERBATIM source quote from the fetched page
    (quote-only rule; every quote is verified by literal containment in the
    fetched text after whitespace/smart-quote normalization — facts whose
    quotes fail verification are DROPPED and the drop is stated in the file);
  - why-this-opener; 1 likely objection + counter; follow-up timing
    (bounded by CHECKLIST rule 8: exactly one follow-up, after 3 days).
If the fetched site differs from the draft's assumptions the dossier states
'SITE DIFFERS FROM DRAFT ASSUMPTIONS' with the differences — surfaced, never
blended into the draft (Rule 7). Drafts with no product URL on file, or whose
site is robots-denied / unfetchable / a JS-only shell, get an explicitly
DEGRADED dossier (no facts section fabricated) — fail loud, never guess.

Part 2 — D2 Phase-1 HN-reply cards. From prospect_pipeline
(~/rick-vault/runtime/rick-runtime.db, read-only: platform='hn-showhn',
empty email in notes JSON), pick the 5 FRESHEST threads (<7 days where
available — story time via the official HN API; see-rate decays weekly),
fetch each product site, and draft a good-citizen feedback comment via
runtime.llm route='writing'. HARD RULES (encoded in the prompt AND enforced
deterministically post-hoc): 2-3 specific verifiable observations about their
actual product; ZERO links; ZERO product/meetrick/roast mention. A draft that
fails the deterministic checks is retried once, then the candidate is skipped.
Cards are written to hn-cards.json beside the drafts for the console to
render. Delivery is Vlad hand-pasting from his PERSONAL account — this script
sends nothing, queues nothing, writes no ledger.

Budget: ~20 analysis + ~5 writing one-shot calls — authorized spend per the
2026-07-19 spec. Canned-fallback LLM output (budget-capped route) is treated
as a failure, never written into a dossier.

Usage:
  python3 scripts/concierge-dossier.py            # everything
  python3 scripts/concierge-dossier.py --only 05  # one draft, no HN cards
  python3 scripts/concierge-dossier.py --skip-hn / --skip-dossiers
Exit: nonzero if any attempted dossier failed or fewer than 5 HN cards were
produced (partial results are still written — fail loud, not silent).
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

BATCH_DIR = Path.home() / "rick-vault" / "go-to-market" / "concierge-batch-2026-07-14"
HN_CARDS_PATH = BATCH_DIR / "hn-cards.json"
DB_PATH = Path.home() / "rick-vault" / "runtime" / "rick-runtime.db"

# Load founder-sourcer.py (dash in name -> importlib). Its import side
# effects are exactly what we want: rick.env setdefault-load + sys.path
# setup for `runtime`, and it exposes the fetch helpers we reuse.
_FS_PATH = Path(__file__).resolve().parent / "founder-sourcer.py"
_spec = importlib.util.spec_from_file_location("founder_sourcer", _FS_PATH)
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)

# Product URL per draft, transcribed from each draft's To/Channel lines and
# the ICP kit (go-to-market/ICP-and-offer-2026-07-13.md) — the only URLs on
# file. "" = no product URL exists on file (PH category page / IH profile
# only): the dossier is written DEGRADED, facts section explicitly empty.
# 20-* is absent on purpose (DO-NOT-SEND synthetic persona).
SITE_URLS = {
    "01": "https://www.producthunt.com/products/govar-english-speaking-app",
    "02": "https://tutoriapp.ai",
    "03": "",  # Vocably — PH language-learning category page only
    "04": "https://www.producthunt.com/products/speaking-ai-language-teacher-lucas",
    "05": "https://univerbal.app",
    "06": "https://apsity.com",
    "07": "https://www.producthunt.com/products/tabai-2",
    "08": "https://getfocusai.com",
    "09": "https://focusunlocker.co.uk",
    "10": "https://play.google.com/store/apps/details?id=com.solilo.solilo",
    "11": "https://play.google.com/store/apps/details?id=me.audiodiary.audiodiary",
    "12": "https://psalmlog.com",
    "13": "",  # Habit Pixel — IH profile / X only
    "14": "",  # HadaBuddy — no App Store id on file
    "15": "",  # GoodTrans — IH post only
    "16": "",  # Five Phrases — PH category page only
    "17": "",  # izTalk — PH category page only
    "18": "",  # VocAdapt — PH category page only
    "19": "https://stayfocus.now",
    "21": "https://www.producthunt.com/products/nada-2",
}

SITE_TEXT_CAP = 6000       # chars of visible page text shown to the model
HN_CARD_TARGET = 5
HN_FRESH_DAYS = 7
HN_CANDIDATE_CAP = 25      # max story-age lookups before giving up
FALLBACK = "DOSSIER-FALLBACK-DO-NOT-USE"
DIFFERS_MARKER = "SITE DIFFERS FROM DRAFT ASSUMPTIONS"

# Deterministic reject patterns for HN comments: any link-ish token or any
# product/self mention kills the draft (D2 hard rules).
HN_LINK_RE = re.compile(
    r"https?://|www\.|\]\(|href=|[a-z0-9-]+\.(?:com|io|ai|app|dev|co|net|org)\b",
    re.IGNORECASE)
HN_PRODUCT_RE = re.compile(r"meetrick|\brick\b|\broast\b|\bvlad\b", re.IGNORECASE)


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def norm(s: str) -> str:
    """Normalize for verbatim-quote containment: smart quotes/dashes ->
    ASCII, whitespace collapsed, casefolded. Content chars are untouched."""
    s = (s.replace("’", "'").replace("‘", "'")
          .replace("“", '"').replace("”", '"')
          .replace("–", "-").replace("—", "-")
          .replace(" ", " "))
    return re.sub(r"\s+", " ", s).strip().casefold()


def fetch_page(url: str) -> dict:
    """Fetch one page via founder-sourcer helpers. Honest statuses only:
    no_url_on_file / robots_denied / fetch_failed / unparseable_spa / ok."""
    out = {"url": url, "status": "no_url_on_file", "title": "",
           "meta": "", "h1": "", "text": ""}
    if not url:
        return out
    if not fs._robots_allows(url):
        out["status"] = "robots_denied"
        return out
    raw = fs._get(url, accept="text/html")
    if raw is None:
        out["status"] = "fetch_failed"
        return out
    page = fs._parse_page(raw)
    visible = " ".join(page.text_parts)
    if len(visible) < 300:
        out["status"] = "unparseable_spa"  # JS shell — never guess from it
        return out
    out.update(status="ok", title=page.title, meta=page.meta_description,
               h1=page.h1s[0] if page.h1s else "",
               text=visible[:SITE_TEXT_CAP])
    return out


def parse_draft(path: Path) -> dict:
    """Title + verbatim body between '**Body:**' and '## Related'. Loud."""
    lines = path.read_text(encoding="utf-8").split("\n")
    title, body_start, body_end = None, None, None
    for i, ln in enumerate(lines):
        if title is None and ln.startswith("# "):
            title = ln[2:].strip()
        elif ln.strip() == "**Body:**" and body_start is None:
            body_start = i + 1
        elif ln.startswith("## Related") and body_start is not None:
            body_end = i
            break
    if title is None or body_start is None or body_end is None:
        raise ValueError(f"{path.name}: draft parse failed")
    return {"title": title,
            "body": "\n".join(lines[body_start:body_end]).strip("\n")}


def llm_json(route: str, prompt: str, *, force_fresh: bool = False) -> dict:
    """One runtime.llm call -> parsed JSON. Canned fallback = failure."""
    from runtime.llm import generate_text
    result = generate_text(route, prompt, FALLBACK, force_fresh=force_fresh)
    text = (result.content or "").strip()
    if result.mode == "fallback" or FALLBACK in text:
        raise RuntimeError(f"route={route} returned canned fallback "
                           f"(mode={result.mode}) — budget-capped or dead")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    data = json.loads(text)  # raises on garbage — caller retries once
    data["_model"] = result.model
    return data


def dossier_prompt(num: str, draft: dict, page: dict) -> str:
    site_block = (
        f"FETCHED SITE ({page['url']}):\n"
        f"<title>: {page['title'][:200]}\nfirst h1: {page['h1'][:200]}\n"
        f"meta description: {page['meta'][:300]}\n"
        f"VISIBLE PAGE TEXT (quotes MUST be copied verbatim from here):\n"
        f"{page['text']}\n"
        if page["status"] == "ok" else
        f"NO SITE CONTENT AVAILABLE (status: {page['status']}). You MUST "
        "return \"facts\": [] and site_matches_draft: null — do not invent "
        "site facts.\n")
    return (
        "You are prepping Vlad (indie founder, sells a $499/mo done-for-you "
        "growth service, first month $249, outcome-guaranteed) for ONE "
        "hand-sent cold outreach. Build a reply-moment dossier for the "
        f"prospect behind draft {num}.\n\n"
        f"THE DRAFT HE WILL SEND (assumptions live here):\n---\n"
        f"# {draft['title']}\n{draft['body']}\n---\n\n" + site_block +
        "\nReturn ONLY a JSON object, no prose, exactly these keys:\n"
        "{\n"
        '  "facts": [  // exactly 3 when site text is available, else []\n'
        '    {"fact": "<=25 words, one concrete site fact useful in a reply",\n'
        '     "quote": "8-25 word VERBATIM substring copied character-for-'
        "character from VISIBLE PAGE TEXT that proves the fact\"}\n"
        "  ],\n"
        '  "site_matches_draft": true|false|null,  // null only when no site text\n'
        '  "site_differences": "empty string, or what on the fetched site '
        "contradicts/outdates the draft's claims — state it plainly\",\n"
        '  "why_this_opener": "1-2 sentences: why the draft\'s opener lands '
        "for THIS prospect, so Vlad can extend the thread in the same "
        "register\",\n"
        '  "objection": "the single most likely objection THIS founder '
        "raises to the $499/$249 pilot\",\n"
        '  "counter": "2-3 sentence counter Vlad can paste-adapt, grounded '
        "in the facts (or the draft if no facts)\",\n"
        '  "follow_up_timing": "when to send the single allowed follow-up '
        "(hard rule: exactly one, after 3 days) — best weekday/time and why\"\n"
        "}\n"
        "Never invent quotes. Never soften a mismatch between site and draft."
    )


def verify_facts(facts: list, page_text: str) -> tuple[list, int]:
    """Keep only facts whose quote passes verbatim containment."""
    ntext = norm(page_text)
    kept, dropped = [], 0
    for f in facts:
        quote = str(f.get("quote", ""))
        if quote and norm(quote) in ntext:
            kept.append({"fact": str(f.get("fact", "")).strip(),
                         "quote": quote.strip()})
        else:
            dropped += 1
    return kept, dropped


def render_dossier(num: str, fname: str, page: dict, data: dict,
                   facts: list, dropped: int) -> str:
    lines = [
        f"# Dossier — {fname}",
        "",
        "<!-- LOCAL ONLY — concierge working file, never publish/Artifact/push -->",
        f"- Generated: {now_iso()} by `scripts/concierge-dossier.py` "
        f"(runtime.llm route=analysis, model {data.get('_model', '?')})",
        f"- Site: {page['url'] or '(no product URL on file)'} — "
        f"fetch status: **{page['status']}**",
        "",
        "## Site check",
    ]
    differs = data.get("site_matches_draft") is False
    if page["status"] != "ok":
        lines.append(
            f"NOT EVALUABLE — site not fetched ({page['status']}). "
            "Personalization line needs your eyeball on the linked page "
            "(CHECKLIST step 2) before sending.")
    elif differs:
        lines.append(f"⚠️ **{DIFFERS_MARKER}**: "
                     f"{data.get('site_differences', '').strip() or '(model gave no detail)'}")
        lines.append("")
        lines.append("The draft was NOT auto-edited — surfaced, never "
                     "blended (Rule 7). Reconcile by hand before sending.")
    else:
        lines.append("OK — fetched site is consistent with the draft's "
                     "assumptions.")
    lines += ["", "## Site facts (each quote verified verbatim against the "
                  "fetched page)"]
    if not facts:
        lines.append("(none — no verified site facts available; nothing "
                     "fabricated in their place)")
    for i, f in enumerate(facts, 1):
        lines.append(f"{i}. {f['fact']}")
        lines.append(f"   > SOURCE QUOTE: \"{f['quote']}\"")
    if dropped:
        lines.append(f"({dropped} fact(s) DROPPED — quote failed verbatim "
                     "verification against the fetched page)")
    lines += [
        "",
        "## Why this opener",
        str(data.get("why_this_opener", "")).strip(),
        "",
        "## Likely objection + counter",
        f"- Objection: {str(data.get('objection', '')).strip()}",
        f"- Counter: {str(data.get('counter', '')).strip()}",
        "",
        "## Follow-up timing (hard rule: exactly one follow-up, after 3 days)",
        str(data.get("follow_up_timing", "")).strip(),
        "",
    ]
    return "\n".join(lines)


def build_dossiers(only: str | None) -> tuple[int, list[str]]:
    failures: list[str] = []
    done = 0
    drafts = sorted(p for p in BATCH_DIR.glob("[0-9][0-9]-*.md")
                    if not p.name.endswith("-dossier.md"))
    for path in drafts:
        num = path.name[:2]
        if num == "20":
            print(f"skip {path.name} — DO-NOT-SEND synthetic persona (spec)")
            continue
        if only and num != only:
            continue
        try:
            draft = parse_draft(path)
            page = fetch_page(SITE_URLS.get(num, ""))
            prompt = dossier_prompt(num, draft, page)
            data, facts, dropped = None, [], 0
            for attempt, fresh in ((1, False), (2, True)):
                try:
                    data = llm_json("analysis", prompt, force_fresh=fresh)
                    facts, dropped = verify_facts(
                        list(data.get("facts") or []), page["text"])
                    if page["status"] == "ok" and len(facts) < 2 and attempt == 1:
                        continue  # too many bad quotes — one fresh retry
                    break
                except (json.JSONDecodeError, RuntimeError) as exc:
                    if attempt == 2:
                        raise
                    print(f"  retry {path.name}: {exc}")
            out_path = BATCH_DIR / f"{num}-dossier.md"
            out_path.write_text(
                render_dossier(num, path.name, page, data, facts, dropped),
                encoding="utf-8")
            done += 1
            tag = ("DIFFERS" if data.get("site_matches_draft") is False
                   else page["status"])
            print(f"wrote {out_path.name} [{tag}] facts={len(facts)} "
                  f"dropped={dropped}")
        except Exception as exc:  # fail loud per draft, keep going
            failures.append(f"{path.name}: {exc}")
            print(f"FAILED {path.name}: {exc}", file=sys.stderr)
    return done, failures


# ---------------------------------------------------------------------------
# Part 2 — D2 Phase-1 HN-reply cards
# ---------------------------------------------------------------------------

def hn_candidates() -> list[dict]:
    """Empty-email hn-showhn prospects, newest sourced first, junk hosts out."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, notes, created_at FROM prospect_pipeline "
            "WHERE platform='hn-showhn' ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
    out = []
    for pid, notes, created_at in rows:
        try:
            n = json.loads(notes)
        except json.JSONDecodeError:
            continue
        domain = str(n.get("domain", ""))
        if n.get("email") or not n.get("story_id") or not domain:
            continue
        if domain in fs.HOST_BLOCKLIST or "github" in domain:
            continue
        out.append({"prospect_id": pid, "domain": domain,
                    "story_id": int(n["story_id"]),
                    "story_title": str(n.get("story_title", "")),
                    "created_at": created_at})
    return out


def story_age_days(cand: dict) -> tuple[float, str, dict]:
    """(age_days, age_source, item) — story time from the HN API where
    available, else sourcing date (labeled, never silently swapped)."""
    item = fs._get_json(f"{fs.HN_API}/item/{cand['story_id']}.json") or {}
    if isinstance(item, dict) and item.get("time"):
        age = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.datetime.fromtimestamp(
                   int(item["time"]), datetime.timezone.utc)).total_seconds() / 86400
        return age, "hn_api", item
    age = (datetime.datetime.now()
           - datetime.datetime.fromisoformat(cand["created_at"])).total_seconds() / 86400
    return age, "sourced_at_fallback", {}


def hn_comment_prompt(cand: dict, page: dict) -> str:
    return (
        "Draft a Hacker News reply comment for this Show HN thread, written "
        "as a thoughtful fellow builder giving good-citizen feedback.\n\n"
        f"Show HN title: {cand['story_title']}\n"
        f"Their product page ({cand['domain']}) — <title>: "
        f"{page['title'][:200]} — h1: {page['h1'][:200]}\n"
        f"VISIBLE PAGE TEXT (the ONLY source of truth):\n{page['text']}\n\n"
        "HARD RULES — every one is checked mechanically, violations are "
        "discarded:\n"
        "1. 2-3 SPECIFIC, verifiable observations about their actual "
        "product, grounded ONLY in the page text above (name a real "
        "feature/wording/pricing detail you saw — no generic praise).\n"
        "2. ZERO links or URLs of any kind. Do not even write a bare "
        "domain name.\n"
        "3. ZERO mention of any product, service, or person of ours — no "
        "meetrick, no Rick, no roast, no Vlad, no pitch, no 'I built', no "
        "'check out'. Nothing to gain but goodwill.\n"
        "4. Sound like genuine Show HN feedback: concrete, warm, one honest "
        "improvement suggestion or question is welcome.\n"
        "5. 60-130 words, plain text, no markdown headers, no emoji.\n"
        "Output ONLY the comment text."
    )


def hn_comment_ok(text: str) -> str:
    """'' if the draft passes the deterministic D2 hard rules, else reason."""
    if HN_LINK_RE.search(text):
        return "contains link/URL/domain"
    if HN_PRODUCT_RE.search(text):
        return "mentions product/self"
    if fs.EMAIL_RE.search(text):
        return "contains email address"
    if not 250 <= len(text) <= 1100:
        return f"length {len(text)} outside 250-1100 chars"
    return ""


def build_hn_cards() -> tuple[int, list[str]]:
    failures: list[str] = []
    cards: list[dict] = []
    aged: list[tuple[float, str, dict, dict]] = []
    for cand in hn_candidates()[:HN_CANDIDATE_CAP]:
        age, age_source, item = story_age_days(cand)
        aged.append((age, age_source, item, cand))
    aged.sort(key=lambda t: t[0])  # freshest thread first — the whole game
    for age, age_source, item, cand in aged:
        if len(cards) >= HN_CARD_TARGET:
            break
        if age >= HN_FRESH_DAYS and len([a for a in aged
                                         if a[0] < HN_FRESH_DAYS]) >= HN_CARD_TARGET:
            continue  # enough fresh ones exist; never pick stale over fresh
        page = fetch_page(f"https://{cand['domain']}")
        if page["status"] != "ok":
            print(f"  hn skip {cand['domain']} — site {page['status']} "
                  "(no verifiable observations possible)")
            continue
        prompt = hn_comment_prompt(cand, page)
        text, reason = "", "no attempt"
        try:
            from runtime.llm import generate_text
            for fresh in (False, True):
                result = generate_text("writing", prompt, FALLBACK,
                                       force_fresh=fresh)
                text = (result.content or "").strip()
                if result.mode == "fallback" or FALLBACK in text:
                    reason = f"canned fallback (mode={result.mode})"
                    break
                reason = hn_comment_ok(text)
                if not reason:
                    break
                print(f"  retry {cand['domain']}: draft rejected — {reason}")
        except Exception as exc:
            reason = str(exc)
        if reason:
            failures.append(f"{cand['domain']} (story {cand['story_id']}): "
                            f"{reason}")
            print(f"  hn FAILED {cand['domain']}: {reason}", file=sys.stderr)
            continue
        cards.append({
            "prospect_id": cand["prospect_id"],
            "story_id": cand["story_id"],
            "story_title": cand["story_title"],
            "hn_thread_url":
                f"https://news.ycombinator.com/item?id={cand['story_id']}",
            "domain": cand["domain"],
            "age_days": round(age, 1),
            "age_source": age_source,
            "hn_points": item.get("score"),
            "hn_comments": item.get("descendants"),
            "comment_text": text,
            "model": result.model,
        })
        print(f"  hn card {len(cards)}/5: {cand['domain']} "
              f"(age {age:.1f}d, {len(text)} chars)")
    HN_CARDS_PATH.write_text(json.dumps({
        "generated_at": now_iso(),
        "rules": "zero links, zero product mention, hand-paste from Vlad's "
                 "personal account ONLY after the 20 concierge sends; STOP "
                 "if any comment is flagged/downvoted-dead",
        "cards": cards,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {HN_CARDS_PATH} — {len(cards)} card(s)")
    return len(cards), failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", metavar="NN", help="one draft number, skips HN")
    ap.add_argument("--skip-dossiers", action="store_true")
    ap.add_argument("--skip-hn", action="store_true")
    args = ap.parse_args()
    if not BATCH_DIR.is_dir():
        sys.exit(f"FATAL: batch dir missing: {BATCH_DIR}")
    failures: list[str] = []
    if not args.skip_dossiers:
        done, fails = build_dossiers(args.only)
        failures += fails
        print(f"dossiers written: {done}, failed: {len(fails)}")
    if not args.skip_hn and not args.only:
        n, fails = build_hn_cards()
        failures += fails
        if n < HN_CARD_TARGET:
            failures.append(f"only {n}/{HN_CARD_TARGET} HN cards produced")
    if failures:
        print("FAILURES (fail loud):\n  " + "\n  ".join(failures),
              file=sys.stderr)
        return 1
    print("all clean — now regenerate the console: "
          "python3 scripts/concierge-console.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
