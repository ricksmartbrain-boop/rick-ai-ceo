#!/usr/bin/env python3
"""founder-sourcer.py — Show HN founder-ICP sourcing engine (WS-B, 2026-07-13).

Replaces the dead Maps-scraped-SMB motion with the ICP that actually engaged:
early-stage SaaS/AI founders. Sources ONLY from the official Hacker News
Firebase API (https://hacker-news.firebaseio.com/v0/) + the founder's own
public pages. No login-walled scraping, no GitHub-scraped emails, no Product
Hunt. Never guesses emails.

Flow:
  1. SOURCE   showstories.json -> items (Show HN, last 14 days, score >= 3).
              Author email/website ONLY if published in their HN 'about'.
              Product site contact email ONLY if plainly published (mailto /
              visible address on homepage or linked contact/about page).
              robots.txt respected (User-Agent meetrick-rick/1.0).
  2. QUALIFY  heuristics: pricing page or launch/beta language, SaaS/AI
              signals; games / pure libraries / hardware are skipped.
              Score 1-5 with reasons.
  3. STORE    upsert into prospect_pipeline (status 'sourced-founder',
              platform 'hn-showhn'); dedupe on email AND domain vs existing
              pipeline rows, outbound_jobs history, outbox/sent drafts, and
              kill_switches suppression + 7d frequency window.
  4. DRAFT    top-scored leads with a published email, HARD CAP 10/day.
              Roast-led value-first email, personalized from the fetched
              HTML. Written to the standard outbox .json contract with
              send_after = now + 2h so the gated handle_outbox_send is the
              ONLY thing that ever sends. Telegram ops gets a preview ping;
              if the ping fails the drafts are flipped to status 'held'
              (fail loud, never silently queue).

Safety:
  - This script NEVER sends email. Sends happen only via the gated outbox
    handler (kill_switches.is_send_allowed) after the 2h review window.
  - Max 10 new cold drafts/day enforced here (state file + outbox scan).
  - Every draft includes the AI disclosure + a working opt-out line.
  - Stdlib-only network I/O (urllib); LLM drafting via runtime.llm with a
    deterministic fallback body, so a dead provider degrades, not breaks.
  - Crash-proof exit 0 so cron/launchd never marks the agent crashed.

State:  ~/rick-vault/operations/founder-sourcer-state.json
Logs:   ~/rick-vault/operations/founder-sourcer.jsonl

CLI:
  --dry-run          no DB writes, no outbox files, no Telegram ping
  --max-drafts N     cap drafts this run (default 10; hard cap 10/day)
  --days N           lookback window (default 14)
  --min-hn-score N   minimum HN story score (default 3)
  --max-site-fetch N max product sites fetched per run (default 25)
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# Make `runtime` + sibling scripts importable when called from cron / launchd.
ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Load rick.env (setdefault only — process env wins). Same pattern as
# founder-discovery-pipeline.py; several cron entry points don't source it.
ENV_CANDIDATES = [
    Path(os.getenv("RICK_ENV_FILE", str(Path.home() / "clawd" / "config" / "rick.env"))),
    ROOT / "config" / "rick.env",
]
for _ec in ENV_CANDIDATES:
    if not _ec.exists():
        continue
    for _line in _ec.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line.startswith("export "):
            _line = _line[7:]
        if "=" in _line and not _line.startswith("#"):
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS_DIR = DATA_ROOT / "operations"
STATE_FILE = OPS_DIR / "founder-sourcer-state.json"
LOG_FILE = OPS_DIR / "founder-sourcer.jsonl"
OUTBOX_DIR = DATA_ROOT / "mailbox" / "outbox"
SENT_DIR = DATA_ROOT / "mailbox" / "sent"

HN_API = "https://hacker-news.firebaseio.com/v0"
USER_AGENT = "meetrick-rick/1.0 (+https://meetrick.ai)"
ROBOTS_AGENT = "meetrick-rick"
HTTP_TIMEOUT = 15
MAX_PAGE_BYTES = 600_000

DAILY_DRAFT_CAP = 10  # HARD RULE: max 10 new cold contacts/day
CTA_URL = (
    "https://meetrick.ai/roast"
    "?utm_source=founder-outreach&utm_medium=email&utm_campaign=showhn"
)
DISCLOSURE = (
    "Full disclosure: I'm Rick, an AI revenue agent — a human (Vlad) "
    "supervises everything I send."
)
OPT_OUT = (
    "P.S. Not your thing? Reply \"no thanks\" and I'll never email you again "
    "— opt-out is instant and permanent."
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
JUNK_EMAIL_BITS = (
    "example.com", "noreply", "no-reply", "donotreply", "sentry",
    "wixpress", "@2x", ".png", ".jpg", ".gif", ".webp", ".svg",
    "your@", "you@", "email@", "name@", "user@domain",
)

# Title-level disqualifiers: games, pure libraries, hardware.
DISQUALIFY_RE = re.compile(
    r"\b(game|roguelike|puzzle|chess|mmorpg|rpg|"
    r"library for|a (?:python|rust|go|js|javascript|c\+\+) library|"
    r"npm package|rust crate|keyboard|raspberry pi|esp32|arduino|"
    r"3d[- ]printed|drone|pcb|robot arm)\b",
    re.IGNORECASE,
)
# Hosts that are never the founder's own product site. github/gitlab are
# also a HARD compliance line: GitHub ToS forbids unsolicited-email use of
# scraped addresses, so repo-hosted Show HNs are skipped entirely.
HOST_BLOCKLIST = {
    "github.com", "gist.github.com", "gitlab.com", "bitbucket.org",
    "npmjs.com", "pypi.org", "crates.io", "sourceforge.net",
    "apps.apple.com", "play.google.com", "chromewebstore.google.com",
    "addons.mozilla.org", "youtube.com", "youtu.be", "medium.com",
    "docs.google.com", "huggingface.co", "news.ycombinator.com",
}

SAAS_AI_RE = re.compile(
    r"\b(ai|saas|api|agent|automat\w*|analytics|dashboard|workflow|crm|"
    r"b2b|for teams|copilot|assistant|no[- ]code|platform)\b",
    re.IGNORECASE,
)
LAUNCH_BETA_RE = re.compile(
    r"\b(beta|waitlist|early access|just launched|launch(?:ing|ed)?|"
    r"free while|preview)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Logging / state (defensive — never raise)
# ---------------------------------------------------------------------------

def now_iso() -> str:
    # Local naive ISO to match engine.now_iso() — outbox send_after is
    # compared lexicographically against it in handle_outbox_send.
    return datetime.now().isoformat(timespec="seconds")


def _ensure_dirs() -> None:
    try:
        OPS_DIR.mkdir(parents=True, exist_ok=True)
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _log(event: str, **fields: Any) -> None:
    try:
        _ensure_dirs()
        rec = {"ts": now_iso(), "event": event, **fields}
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _read_state() -> dict:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("seen_story_ids", [])
                data.setdefault("drafts_by_day", {})
                return data
    except Exception:
        pass
    return {"seen_story_ids": [], "drafts_by_day": {}}


def _write_state(state: dict) -> None:
    try:
        _ensure_dirs()
        state["seen_story_ids"] = state.get("seen_story_ids", [])[-2000:]
        state["updated_at"] = now_iso()
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# HTTP (stdlib only)
# ---------------------------------------------------------------------------

def _get(url: str, *, accept: str = "*/*", timeout: int = HTTP_TIMEOUT) -> str | None:
    """GET url -> text (max MAX_PAGE_BYTES) or None on any error."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": accept}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(MAX_PAGE_BYTES).decode("utf-8", errors="replace")
    except Exception as e:
        _log("http.error", url=url[:200], error=str(e)[:160])
        return None


def _get_json(url: str) -> Any:
    raw = _get(url, accept="application/json")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        _log("http.bad_json", url=url[:200])
        return None


_ROBOTS_CACHE: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def _robots_allows(url: str) -> bool:
    """True unless the site's robots.txt explicitly denies our UA the path.

    200 -> parse and honor. 404/no file -> allow (standard). Network error
    fetching robots.txt -> conservative skip (False)."""
    try:
        parts = urllib.parse.urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in _ROBOTS_CACHE:
            rp: urllib.robotparser.RobotFileParser | None
            try:
                req = urllib.request.Request(
                    base + "/robots.txt", headers={"User-Agent": USER_AGENT}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = resp.read(200_000).decode("utf-8", errors="replace")
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(body.splitlines())
            except urllib.error.HTTPError as e:
                rp = None if e.code >= 500 else urllib.robotparser.RobotFileParser()
                if rp is not None:
                    rp.parse([])  # 4xx -> no rules -> allow all
            except Exception:
                rp = None  # network failure -> skip the site entirely
            _ROBOTS_CACHE[base] = rp
        rp = _ROBOTS_CACHE[base]
        if rp is None:
            return False
        return rp.can_fetch(ROBOTS_AGENT, url)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# HTML parsing (stdlib HTMLParser — no deps)
# ---------------------------------------------------------------------------

class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta_description = ""
        self.h1s: list[str] = []
        self.links: list[tuple[str, str]] = []  # (href, text)
        self.mailtos: list[str] = []
        self.text_parts: list[str] = []
        self._stack: list[str] = []
        self._cur_href: str | None = None
        self._cur_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        self._stack.append(tag)
        if tag == "meta" and str(a.get("name", "")).lower() == "description":
            self.meta_description = (a.get("content") or "").strip()
        if tag == "a":
            href = (a.get("href") or "").strip()
            if href.lower().startswith("mailto:"):
                addr = href[7:].split("?", 1)[0].strip()
                if addr:
                    self.mailtos.append(addr)
            self._cur_href = href
            self._cur_link_text = []

    def handle_endtag(self, tag: str) -> None:
        while self._stack and self._stack.pop() != tag:
            pass
        if tag == "a" and self._cur_href is not None:
            self.links.append((self._cur_href, " ".join(self._cur_link_text).strip()))
            self._cur_href = None
            self._cur_link_text = []

    def handle_data(self, data: str) -> None:
        if any(t in ("script", "style", "noscript") for t in self._stack[-3:]):
            return
        chunk = data.strip()
        if not chunk:
            return
        if "title" in self._stack and not self.title:
            self.title = chunk[:200]
            return
        if "h1" in self._stack:
            self.h1s.append(chunk[:200])
        if self._cur_href is not None:
            self._cur_link_text.append(chunk)
        self.text_parts.append(chunk)


def _parse_page(raw_html: str) -> _PageParser:
    p = _PageParser()
    try:
        p.feed(raw_html)
    except Exception:
        pass
    return p


def _clean_emails(candidates: list[str]) -> list[str]:
    out: list[str] = []
    for c in candidates:
        c = c.strip().strip(".,;:<>()[]\"'").lower()
        if not c or "@" not in c:
            continue
        if any(bit in c for bit in JUNK_EMAIL_BITS):
            continue
        if c not in out:
            out.append(c)
    return out


def _domain_of(url: str) -> str:
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
        return host.lower().removeprefix("www.")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 1. SOURCE — official HN Firebase API only
# ---------------------------------------------------------------------------

def fetch_show_stories(days: int, min_score: int, seen: set[int]) -> list[dict]:
    ids = _get_json(f"{HN_API}/showstories.json") or []
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    stories: list[dict] = []
    for sid in ids:
        if not isinstance(sid, int) or sid in seen:
            continue
        item = _get_json(f"{HN_API}/item/{sid}.json")
        if not isinstance(item, dict) or item.get("deleted") or item.get("dead"):
            continue
        if item.get("type") != "story":
            continue
        title = str(item.get("title") or "")
        if not title.lower().startswith("show hn"):
            continue
        if (item.get("time") or 0) < cutoff or (item.get("score") or 0) < min_score:
            continue
        url = str(item.get("url") or "").strip()
        if not url:  # text-only Show HN — no product site to look at
            continue
        stories.append(
            {
                "story_id": sid,
                "title": title,
                "url": url,
                "author": str(item.get("by") or ""),
                "hn_score": int(item.get("score") or 0),
            }
        )
    return stories


def fetch_author_contact(author: str) -> dict:
    """Email/website ONLY if the founder chose to publish it in their
    public HN profile 'about' field."""
    out = {"email": "", "website": ""}
    if not author:
        return out
    user = _get_json(f"{HN_API}/user/{urllib.parse.quote(author)}.json")
    if not isinstance(user, dict):
        return out
    about = html_lib.unescape(re.sub(r"<[^>]+>", " ", str(user.get("about") or "")))
    emails = _clean_emails(EMAIL_RE.findall(about))
    if emails:
        out["email"] = emails[0]
    m = re.search(r"https?://[^\s\"'<>]+", about)
    if m:
        out["website"] = m.group(0).rstrip(".,)")
    return out


def fetch_site_facts(url: str) -> dict:
    """Fetch the product homepage (+ up to 2 linked contact/about pages).
    Returns facts for qualification + personalization. Respects robots.txt,
    skips SPAs we can't parse. Emails only if plainly published."""
    facts: dict[str, Any] = {
        "fetched": False, "parseable": False, "title": "", "meta_description": "",
        "h1": "", "pricing_found": False, "launch_beta": False, "saas_ai": False,
        "emails": [], "text_excerpt": "",
    }
    if not _robots_allows(url):
        _log("site.robots_denied_or_unreachable", url=url[:200])
        return facts
    raw = _get(url, accept="text/html")
    if raw is None:
        return facts
    facts["fetched"] = True
    page = _parse_page(raw)
    visible = " ".join(page.text_parts)
    if len(visible) < 300:
        # JS-only shell we can't honestly personalize from — skip, don't guess.
        _log("site.spa_unparseable", url=url[:200], visible_chars=len(visible))
        return facts
    facts["parseable"] = True
    facts["title"] = page.title
    facts["meta_description"] = page.meta_description
    facts["h1"] = page.h1s[0] if page.h1s else ""
    facts["text_excerpt"] = visible[:1500]
    lower_all = (visible + " " + " ".join(h for h, _t in page.links)).lower()
    facts["pricing_found"] = "pricing" in lower_all or "/plans" in lower_all
    facts["launch_beta"] = bool(LAUNCH_BETA_RE.search(visible))
    facts["saas_ai"] = bool(SAAS_AI_RE.search(page.title + " " + visible[:3000]))

    emails = list(page.mailtos) + EMAIL_RE.findall(visible)

    # Up to 2 same-host contact/about pages, robots permitting.
    host = _domain_of(url)
    extra = 0
    for href, _text in page.links:
        if extra >= 2 or not re.search(r"contact|about|support", href, re.I):
            continue
        full = urllib.parse.urljoin(url, href)
        if _domain_of(full) != host or not _robots_allows(full):
            continue
        extra += 1
        sub_raw = _get(full, accept="text/html")
        if sub_raw:
            sub = _parse_page(sub_raw)
            emails += list(sub.mailtos) + EMAIL_RE.findall(" ".join(sub.text_parts))

    facts["emails"] = _clean_emails(emails)
    return facts


# ---------------------------------------------------------------------------
# 2. QUALIFY — heuristics, score 1-5 with reasons
# ---------------------------------------------------------------------------

def qualify(story: dict, facts: dict, email: str) -> tuple[int, list[str]]:
    """Returns (score 0-5, reasons). 0 = disqualified."""
    blob = story["title"] + " " + facts.get("title", "") + " " + facts.get("text_excerpt", "")[:500]
    m = DISQUALIFY_RE.search(blob)
    if m:
        return 0, [f"disqualified:{m.group(0).lower()}"]
    if not facts.get("parseable"):
        return 0, ["disqualified:site_unparseable_or_robots"]

    score, reasons = 1, ["show-hn-early-stage"]
    if facts.get("pricing_found"):
        score += 1
        reasons.append("pricing-page")
    if facts.get("launch_beta"):
        score += 1
        reasons.append("launch/beta-language")
    if facts.get("saas_ai") or SAAS_AI_RE.search(story["title"]):
        score += 1
        reasons.append("saas/ai-signal")
    if email:
        score += 1
        reasons.append("published-contact-email")
    if not (facts.get("pricing_found") or facts.get("launch_beta")):
        reasons.append("weak:no-pricing-no-launch-language")
    return min(score, 5), reasons


# ---------------------------------------------------------------------------
# 3. STORE — dedupe + upsert into prospect_pipeline
# ---------------------------------------------------------------------------

def load_known_sets(conn) -> tuple[set[str], set[str], str]:
    """(known_emails, known_domains, outbound_blob) from pipeline history,
    outbound_jobs history, and outbox/sent drafts."""
    emails: set[str] = set()
    domains: set[str] = set()
    for row in conn.execute("SELECT username, profile_url, notes FROM prospect_pipeline"):
        for cand in EMAIL_RE.findall((row[0] or "") + " " + (row[2] or "")):
            emails.add(cand.lower())
        d = _domain_of(row[1] or "")
        if d:
            domains.add(d)
        try:
            nd = json.loads(row[2] or "{}").get("domain", "")
            if nd:
                domains.add(str(nd).lower())
        except Exception:
            pass
    blob_parts = [r[0] or "" for r in conn.execute("SELECT payload_json FROM outbound_jobs")]
    for mdir in (OUTBOX_DIR, SENT_DIR):
        if mdir.exists():
            for f in mdir.glob("*.json"):
                try:
                    msg = json.loads(f.read_text(encoding="utf-8"))
                    if msg.get("to"):
                        emails.add(str(msg["to"]).lower())
                    if msg.get("domain"):
                        domains.add(str(msg["domain"]).lower())
                except Exception:
                    continue
    return emails, domains, "\n".join(blob_parts).lower()


def upsert_prospect(conn, lead: dict) -> str:
    """Insert or refresh our own row. Returns 'inserted' | 'updated'."""
    pid = "pr_" + hashlib.sha1(lead["domain"].encode()).hexdigest()[:12]
    lead["id"] = pid
    notes = json.dumps(
        {
            "email": lead["email"], "domain": lead["domain"],
            "story_id": lead["story_id"], "story_title": lead["title"],
            "hn_score": lead["hn_score"], "score_reasons": lead["reasons"],
            "contact_source": lead["contact_source"], "source": "hn-showhn",
        },
        ensure_ascii=False,
    )
    ts = now_iso()
    row = conn.execute("SELECT id FROM prospect_pipeline WHERE id = ?", (pid,)).fetchone()
    if row:
        conn.execute(
            "UPDATE prospect_pipeline SET score = ?, notes = ?, updated_at = ? WHERE id = ?",
            (float(lead["score"]), notes, ts, pid),
        )
        return "updated"
    conn.execute(
        """INSERT INTO prospect_pipeline
           (id, platform, username, profile_url, score, status, notes, created_at, updated_at)
           VALUES (?, 'hn-showhn', ?, ?, ?, 'sourced-founder', ?, ?, ?)""",
        (pid, lead["author"], lead["url"], float(lead["score"]), notes, ts, ts),
    )
    return "inserted"


# ---------------------------------------------------------------------------
# 4. DRAFT — roast-led value-first email into the gated outbox
# ---------------------------------------------------------------------------

# Greeting policy (2026-07-16): drafts used to open with the raw HN username
# ("Hey asm28208 —", "Hey workplace_1 —") — an instant bot-tell. A first name
# is used ONLY when confidently derivable; everything else gets the neutral
# "Hey —". Deterministic on purpose (rule 5: code answers, not a model).
FIRST_NAMES = frozenset("""
    aaron adam adrian ahmed alan albert alberto alejandro alex alexander
    alexandra alexei alexis ali alice amanda amir amit amy ana anders andre
    andrea andreas andrei andres andrew andy angela anil anita anna anne
    anthony anton antonio arjun arthur arun ashley barbara ben benjamin bill
    bob boris brad brandon brian bruce bruno bryan cameron carl carla carlos
    carol caroline catherine chad charles charlie chen chloe chris christian
    christina christine christopher cindy claire clara claude claudia colin
    connor craig dale damien dan dana daniel daniela danny dario darren dave
    david dean dennis derek diana diego dinesh dmitri dmitry dominic don
    donald donna doug douglas duncan dustin dylan eddie edgar eduardo edward
    elena eli elias elizabeth ellen emil emily emma enrique eric erik erin
    ethan eugene eva evan fabian felipe felix fernando filip florian
    francesco francis francisco frank fred gabriel gary gavin geoffrey george
    giovanni glenn gordon grace graham grant greg gregory guido guillaume
    hannah hans harold harry hassan heather hector helen henrik henry holly
    howard hugo ian igor ilya irene isaac isabel ivan jack jackson jacob
    jacques jaime jake james jamie jan jane janet jason javier jay jean jeff
    jeffrey jennifer jenny jeremy jerome jerry jesse jessica jill jim jimmy
    joanna joe joel johan johannes john johnny jon jonas jonathan jordan
    jorge jose josef joseph josh joshua juan judith julia julian julie
    julien justin kai karen karl kate katherine kathleen kathy katie keith
    kelly ken kenneth kevin kim kirill klaus kumar kurt kyle lars laura
    lauren laurent lawrence lee leon leonard leonardo leslie liam linda lisa
    logan lorenzo louis luca lucas luis luke madison manuel marc marcel
    marco marcos marcus margaret maria marie marina mario marius mark marko
    martin mary mateo matt matthew matthias mauricio max maxim maya megan
    mehmet melissa michael michel michelle miguel mikael mike mikhail milan
    miles mohamed mohammed monica morgan moritz nadia nancy natalia natalie
    nathan neil nick nicolas nicole nikhil niklas nikolai nina noah nora
    norman oliver olivia omar oscar owen pablo pamela paolo pascal patricia
    patrick paul paula paulo pavel pedro peter phil philip philipp philippe
    pierre piotr priya rachel rafael rahul raj rajesh ralph ramon randy raul
    ravi ray raymond rebecca ricardo richard rick rob robert roberto robin
    rodrigo roger roland roman ron ronald ross roy ruben russell ruth ryan
    sam samantha samir samuel sandeep sandra sanjay sara sarah sasha scott
    sean sebastian sergei sergey sergio seth shane shannon sharon shawn
    simon simone sofia sonia sophia sophie stefan stefano stephanie stephen
    steve steven stuart sunil suresh susan sven tanya tara ted teresa terry
    theo thomas tiago tim timothy tina tobias todd tom tomas tommy tony
    travis trevor tristan tyler valentin vanessa victor victoria vijay
    vikram vincent vinod vlad vladimir walter wayne wei wendy werner wesley
    will william wolfgang xavier yann yuri yusuf zach zachary
""".split())


def greeting_name(username: str, provided: str = "") -> str:
    """First name for a cold-draft greeting, or "" when not confidently
    derivable. A name comes ONLY from (a) `provided` — a display name the
    founder's own page/profile published — or (b) an HN username that IS a
    plausible first name: pure alpha, 3-12 chars, and in FIRST_NAMES.
    A string containing digits or underscores is NEVER used as a name."""
    tokens = (provided or "").split()
    if tokens:
        first = tokens[0].strip(".,")
        if first.isalpha() and 2 <= len(first) <= 20:
            return first[0].upper() + first[1:]
    u = (username or "").strip().lower()
    if u.isalpha() and 3 <= len(u) <= 12 and u in FIRST_NAMES:
        return u.capitalize()
    return ""


def greeting(username: str, provided: str = "") -> str:
    """Greeting line: 'Hey Daniel —' when a name is derivable, else 'Hey —'."""
    name = greeting_name(username, provided)
    return f"Hey {name} —" if name else "Hey —"


def _observations(facts: dict, story: dict) -> list[str]:
    obs: list[str] = []
    h1 = facts.get("h1") or facts.get("title") or ""
    if h1:
        obs.append(
            f"Your homepage leads with “{h1[:90]}” — I read it twice; "
            "a cold visitor gives it one."
        )
    if not facts.get("meta_description"):
        obs.append(
            "No meta description on the homepage — Google is writing your "
            "pitch for you right now, and it's not their best work."
        )
    if facts.get("pricing_found"):
        obs.append(
            "You actually show pricing — rarer on Show HN than it should "
            "be, and it's the page I'd tighten first."
        )
    else:
        obs.append(
            "I couldn't find pricing — hidden pricing quietly kills "
            "signups from people who are already convinced."
        )
    if facts.get("launch_beta"):
        obs.append(
            "You're in launch/beta mode — the exact moment funnel leaks "
            "are cheapest to fix."
        )
    return obs[:3]


def _fallback_body(lead: dict, facts: dict) -> str:
    obs = _observations(facts, lead)
    obs_md = "\n".join(f"- {o}" for o in obs)
    product = lead["title"].removeprefix("Show HN:").strip() or lead["domain"]
    return (
        f"**Subject:** roasted {lead['domain']} (free, takes you 0 minutes)\n\n"
        f"{greeting(lead['author'])}\n\n"
        f"Saw “{lead['title']}” on Show HN and poked around {lead['domain']}. "
        f"A few things jumped out:\n\n"
        f"{obs_md}\n\n"
        f"I do deep landing-page + funnel roasts for early-stage founders — "
        f"what's costing you signups and what to fix first, no fluff. "
        f"Want one for {product}? It's free: {CTA_URL}\n\n"
        f"{DISCLOSURE}\n\n"
        f"— Rick\n\n"
        f"{OPT_OUT}\n"
    )


def draft_email(lead: dict, facts: dict) -> str:
    """LLM-drafted body (route=writing) with deterministic fallback. The
    output MUST carry subject line, CTA, disclosure and opt-out — anything
    missing means we use the fallback instead (predictable > clever)."""
    fallback = _fallback_body(lead, facts)
    greet = greeting(lead["author"])
    prompt = (
        "Draft a short cold outreach email (under 180 words) from Rick, an AI "
        "revenue agent at meetrick.ai. Voice: direct, a little wry, zero "
        "corporate filler. Recipient: the founder below, who just posted on "
        "Show HN.\n\n"
        f"Show HN title: {lead['title']}\n"
        f"Product domain: {lead['domain']}\n"
        "Facts observed on their real landing page (use ONLY these, do not "
        "invent anything):\n"
        f"- <title> tag: {facts.get('title', '')[:150]}\n"
        f"- first h1: {facts.get('h1', '')[:150]}\n"
        f"- meta description: {facts.get('meta_description', '')[:200] or '(missing)'}\n"
        f"- pricing page found: {facts.get('pricing_found')}\n"
        f"- launch/beta language: {facts.get('launch_beta')}\n"
        f"- page text excerpt: {facts.get('text_excerpt', '')[:800]}\n\n"
        "Requirements (all mandatory):\n"
        "1. Start with a line exactly like: **Subject:** <subject>\n"
        "2. Open the body with exactly this greeting line: " + greet + "\n"
        "   Never greet by HN username and never invent a name.\n"
        "3. 2-3 SPECIFIC observations about THEIR page from the facts above.\n"
        "4. Offer a free deep roast with exactly one CTA link: " + CTA_URL + "\n"
        "5. Include this disclosure verbatim: " + DISCLOSURE + "\n"
        "6. End with this opt-out verbatim: " + OPT_OUT + "\n"
        "Output ONLY the email body markdown, nothing else."
    )
    try:
        from runtime.llm import generate_text
        result = generate_text("writing", prompt, fallback)
        body = (result.content or "").strip()
    except Exception as e:
        _log("draft.llm_error", domain=lead["domain"], error=str(e)[:160])
        return fallback
    # `greet` in required markers: a draft that greets any other way (e.g. by
    # raw HN username) is rejected and the fallback — which always greets
    # correctly — ships instead.
    required = ("**Subject:**", greet, CTA_URL, "Vlad", "no thanks")
    if all(marker in body for marker in required) and len(body) < 4000:
        return body + ("\n" if not body.endswith("\n") else "")
    _log("draft.llm_output_rejected", domain=lead["domain"], length=len(body))
    return fallback


def _apply_subject_variant(conn, body: str, lead: dict) -> tuple[str, str]:
    """WS-F variant hook (2026-07-13): Thompson-pick a subject-line variant
    from skill_variants (skill='founder_outreach_subject', seeded 2026-07-13)
    and swap it into the draft's **Subject:** line. Deterministic — the body
    stays as drafted, only the subject is A/B-controlled so reply linkage
    can credit the winning subject. Returns (body, variant_id); variant_id
    is '' when no variant was applied (picker unavailable / no Subject line),
    so nothing gets credited for a subject that never shipped."""
    try:
        from runtime.variants import pick_variant
        picked = pick_variant(conn, "founder_outreach_subject")
    except Exception as e:
        _log("variant.pick_error", error=str(e)[:120])
        picked = None
    if not picked:
        return body, ""
    company = lead["title"].removeprefix("Show HN:").strip() or lead["domain"]
    subject = picked["prompt_text"].replace("{company}", company)[:120]
    lines = body.splitlines()
    for i, ln in enumerate(lines[:5]):
        if ln.startswith("**Subject:**"):
            lines[i] = f"**Subject:** {subject}"
            out = "\n".join(lines)
            if body.endswith("\n") and not out.endswith("\n"):
                out += "\n"
            return out, picked["variant_id"]
    return body, ""


def write_outbox_draft(lead: dict, body: str, subject_variant_id: str = "") -> Path:
    send_after = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
    slug = re.sub(r"[^a-z0-9]+", "-", lead["domain"].lower()).strip("-")
    path = OUTBOX_DIR / f"founder-{slug}-{datetime.now():%Y%m%d}.json"
    payload = {
        "to": lead["email"],
        "status": "pending",
        "body_markdown": body,
        "send_after": send_after,
        "source": "founder-sourcer",
        "campaign": "showhn",
        "lead_id": lead["id"],
        "domain": lead["domain"],
        "product": lead["title"],
        "cold": True,
        "created_at": now_iso(),
    }
    if subject_variant_id:
        # WS-F linkage: reply-credit the winning subject variant.
        payload["subject_variant_id"] = subject_variant_id
        payload["subject_skill"] = "founder_outreach_subject"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _hold_drafts(paths: list[Path], reason: str) -> None:
    for p in paths:
        try:
            msg = json.loads(p.read_text(encoding="utf-8"))
            msg["status"] = "held"
            msg["held_reason"] = reason
            p.write_text(json.dumps(msg, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Show HN founder-ICP sourcer (WS-B)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-drafts", type=int, default=DAILY_DRAFT_CAP)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--min-hn-score", type=int, default=3)
    ap.add_argument("--max-site-fetch", type=int, default=25)
    args = ap.parse_args()

    _ensure_dirs()
    state = _read_state()
    today = datetime.now().strftime("%Y-%m-%d")
    drafts_today = int(state.get("drafts_by_day", {}).get(today, 0))
    # Belt-and-braces: also count today's founder-* files already in outbox.
    on_disk_today = len(list(OUTBOX_DIR.glob(f"founder-*-{datetime.now():%Y%m%d}.json")))
    drafts_today = max(drafts_today, on_disk_today)
    draft_budget = max(0, min(args.max_drafts, DAILY_DRAFT_CAP) - drafts_today)

    _log("run.start", dry_run=args.dry_run, drafts_today=drafts_today,
         draft_budget=draft_budget, days=args.days, min_hn_score=args.min_hn_score)

    seen = set(int(x) for x in state.get("seen_story_ids", []) if str(x).isdigit())
    stories = fetch_show_stories(args.days, args.min_hn_score, seen)
    _log("source.stories", count=len(stories))

    from runtime.db import connect as runtime_connect
    from runtime.kill_switches import is_suppressed_address, last_send_ts

    conn = runtime_connect()
    known_emails, known_domains, outbound_blob = load_known_sets(conn)

    counts = {"stories": len(stories), "qualified": 0, "inserted": 0, "updated": 0,
              "deduped": 0, "disqualified": 0, "drafted": 0, "held": 0}
    leads: list[dict] = []
    fetched_sites = 0
    run_domains: set[str] = set()

    for story in stories:
        domain = _domain_of(story["url"])
        if not domain:
            state["seen_story_ids"].append(story["story_id"])
            continue
        # Exact blocklist + code-forge page-host suffixes (github.io /
        # gitlab.io / *.pages.dev repo sites): these are personal-project
        # hosts, not fundable products, AND the ToS compliance line (rule 3).
        if domain in HOST_BLOCKLIST or domain.endswith((".github.io", ".gitlab.io")) or domain in ("github.io", "gitlab.io"):
            state["seen_story_ids"].append(story["story_id"])
            counts["disqualified"] += 1
            _log("qualify.skip_host", domain=domain, title=story["title"][:120])
            continue
        # Cheap disqualify on title before spending a site fetch.
        if DISQUALIFY_RE.search(story["title"]):
            counts["disqualified"] += 1
            state["seen_story_ids"].append(story["story_id"])
            _log("qualify.skip_title", domain=domain, title=story["title"][:120])
            continue
        if domain in run_domains:
            state["seen_story_ids"].append(story["story_id"])
            continue
        run_domains.add(domain)
        # Dedupe BEFORE fetching: domain vs pipeline + outbound history.
        if domain in known_domains or domain in outbound_blob:
            counts["deduped"] += 1
            state["seen_story_ids"].append(story["story_id"])
            _log("dedupe.domain", domain=domain)
            continue
        if fetched_sites >= args.max_site_fetch:
            # NOT marked seen — the fetch cap is a per-run budget, and the
            # story deserves a real look on the next run.
            continue
        fetched_sites += 1
        state["seen_story_ids"].append(story["story_id"])

        facts = fetch_site_facts(story["url"])
        author = fetch_author_contact(story["author"])
        email, contact_source = "", ""
        if author["email"]:
            email, contact_source = author["email"], "hn-profile-about"
        elif facts["emails"]:
            email, contact_source = facts["emails"][0], "product-site"

        if email:
            # Rule 3: reject addresses that betray a code-forge origin
            # (e.g. "…github@gmail.com") — those come from GitHub profiles,
            # whose ToS forbids unsolicited-email use.
            localpart = email.split("@", 1)[0].lower()
            if "github" in localpart or "gitlab" in localpart:
                counts["disqualified"] += 1
                _log("qualify.skip_forge_email", domain=domain)
                continue
            # 2026-07-16: the send gate (kill_switches → email_validator
            # is_role_account) permanently blocks role inboxes — drafting
            # them just strands outbox items. Same check, at source.
            from runtime.email_validator import is_role_account
            if is_role_account(email):
                counts["disqualified"] += 1
                _log("qualify.skip_role_account", domain=domain)
                continue
            if email in known_emails or is_suppressed_address(email):
                counts["deduped"] += 1
                _log("dedupe.email", domain=domain)
                continue
            last = last_send_ts(email)
            if last is not None and (datetime.now() - last.replace(tzinfo=None)) < timedelta(days=7):
                counts["deduped"] += 1
                _log("dedupe.frequency_7d", domain=domain)
                continue

        score, reasons = qualify(story, facts, email)
        if score == 0:
            counts["disqualified"] += 1
            _log("qualify.skip", domain=domain, reasons=reasons)
            continue

        counts["qualified"] += 1
        lead = dict(story)
        lead.update({"domain": domain, "email": email, "score": score,
                     "reasons": reasons, "contact_source": contact_source,
                     "facts": facts})
        leads.append(lead)
        _log("qualify.keep", domain=domain, score=score, reasons=reasons,
             has_email=bool(email))

    # STORE
    if not args.dry_run:
        for lead in leads:
            outcome = upsert_prospect(conn, lead)
            counts[outcome] += 1
        conn.commit()
    else:
        for lead in leads:
            lead["id"] = "pr_dryrun"

    # DRAFT — top-scored, email required, score >= 3, hard daily cap.
    drafted_paths: list[Path] = []
    preview_lines: list[str] = []
    if not args.dry_run and draft_budget > 0:
        candidates = sorted(
            (l for l in leads if l["email"] and l["score"] >= 3),
            key=lambda l: (-l["score"], -l["hn_score"]),
        )
        for lead in candidates[:draft_budget]:
            body = draft_email(lead, lead["facts"])
            # WS-F (2026-07-13): Thompson-picked subject variant replaces the
            # LLM subject so replies can credit the winning subject line.
            body, subject_variant = _apply_subject_variant(conn, body, lead)
            path = write_outbox_draft(lead, body, subject_variant)
            drafted_paths.append(path)
            counts["drafted"] += 1
            subject = next(
                (ln.replace("**Subject:**", "").strip()
                 for ln in body.splitlines() if ln.startswith("**Subject:**")),
                "(no subject)",
            )
            # WS-F: queued touch in outbound_jobs. handle_outbox_send flips it
            # to sent (via outbox_file); reply-watcher links replies back and
            # credits the variant a win. Shielded — ledger failure never
            # blocks drafting.
            try:
                from runtime.touch_log import log_touch
                log_touch(
                    conn, to=lead["email"], channel="email",
                    template_id=f"founder:{subject_variant or 'llm_subject'}",
                    subject=subject if subject != "(no subject)" else "",
                    variant=subject_variant,
                    skill="founder_outreach_subject" if subject_variant else "",
                    source="founder-sourcer", status="queued",
                    outbox_file=path.name,
                )
            except Exception as e:
                _log("touch_log.error", domain=lead["domain"], error=str(e)[:120])
            preview_lines.append(
                f"{len(drafted_paths)}. {lead['author']} — "
                f"{lead['title'].removeprefix('Show HN:').strip()[:60]} "
                f"({lead['domain']}) — “{subject[:70]}”"
            )
            _log("draft.queued", domain=lead["domain"], to=lead["email"][:80],
                 file=str(path), score=lead["score"], variant=subject_variant)
        state.setdefault("drafts_by_day", {})[today] = drafts_today + counts["drafted"]

    # Telegram preview ping — HARD RULE: owner gets a veto window. If the
    # ping fails, drafts are flipped to 'held' so nothing sends unreviewed.
    if drafted_paths:
        send_after_h = (datetime.now() + timedelta(hours=2)).strftime("%H:%M")
        msg = (
            f"\U0001f50e founder-sourcer (Show HN): {len(drafted_paths)} roast "
            f"drafts queued → outbox, gated send after {send_after_h} "
            f"(2h veto window)\n" + "\n".join(preview_lines) +
            "\nVeto: delete founder-*.json in ~/rick-vault/mailbox/outbox "
            "or set status:'held'."
        )
        ping_ok = False
        try:
            import tg_notify
            ping_ok = tg_notify.send("ops-alerts", msg)
        except Exception as e:
            _log("telegram.error", error=str(e)[:160])
        if not ping_ok:
            _hold_drafts(drafted_paths, "telegram_preview_ping_failed")
            counts["held"] = len(drafted_paths)
            counts["drafted"] = 0
            _log("telegram.ping_failed_drafts_held", count=len(drafted_paths))
            print("founder-sourcer: TELEGRAM PING FAILED — drafts HELD, not pending.",
                  file=sys.stderr)
        else:
            _log("telegram.ping_ok", count=len(drafted_paths))

    if not args.dry_run:
        _write_state(state)
    conn.close()

    summary = (
        f"founder-sourcer: stories={counts['stories']} qualified={counts['qualified']} "
        f"inserted={counts['inserted']} updated={counts['updated']} "
        f"deduped={counts['deduped']} disqualified={counts['disqualified']} "
        f"drafted={counts['drafted']} held={counts['held']} "
        f"(dry_run={args.dry_run}, daily_cap_left={draft_budget - counts['drafted']})"
    )
    print(summary)
    _log("run.done", **counts, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        _log("run.crash", error=str(e)[:300])
        print(f"founder-sourcer: fatal {e}", file=sys.stderr)
        # Exit 0 so cron/launchd never marks the agent as crashed.
        raise SystemExit(0)
