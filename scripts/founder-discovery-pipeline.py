#!/usr/bin/env python3
"""
founder-discovery-pipeline.py
Founder ICP discovery at scale: scrape GitHub trending + ProductHunt + HN Show HN,
load existing lead files, score via opus-4-7 (route=review), generate personalized
openers, and insert qualified_lead workflows.

Usage:
  python3 scripts/founder-discovery-pipeline.py [--dry-run] [--limit 50]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load env
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

from runtime.llm import generate_text  # noqa: E402

VAULT = Path.home() / "rick-vault"
LEADS_DIR = VAULT / "projects" / "qualified-leads"
LEADS_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "Rick-FounderDiscovery/1.0 (+https://meetrick.ai)"
NOW_UTC = datetime.now(timezone.utc).isoformat(timespec="seconds")

# ── Hard-coded suppression sets ─────────────────────────────────────────────
BLOCKED_EMAILS: set[str] = {
    "user@domain.com",
    "rick@meetrick.ai",
    "vladislav@belkins.io",
    "vlad@belkins.io",
    "paul25011991z@gmail.com",
    "hello@producthunt.com",
    "hello@digest.producthunt.com",
    "crew@morningbrew.com",
    "notifications-noreply@linkedin.com",
    "messages-noreply@linkedin.com",
    "updates-noreply@linkedin.com",
    "no-reply@accounts.google.com",
    "noreply@payoneer.com",
    "noreply@reddit.com",
    "analytics-noreply@google.com",
    "security@mail.instagram.com",
    "updates@e.stripe.com",
}

# Local SMB blocklist (the newly-shipped blocklist from MEMORY)
LOCAL_SMB_SIGNALS = {
    "chiro", "chiropractic", "clinic", "dental", "dentist", "med spa", "massage",
    "wellness", "acupuncture", "patient", "patients", "law firm", "lawyer", "attorney",
    "restaurant", "menu", "reservation", "booking", "bookings", "front desk", "salon",
    "barbershop", "gym", "personal trainer", "chiropractor", "optometry", "ophthalmology",
    "pediatric", "veterinary", "vet clinic", "real estate agent", "mortgage broker",
}


def _is_local_smb(text: str) -> bool:
    low = text.lower()
    return any(s in low for s in LOCAL_SMB_SIGNALS)


# ── Suppression loading ──────────────────────────────────────────────────────

def load_suppression_set() -> set[str]:
    suppressed: set[str] = set(BLOCKED_EMAILS)

    # email-bounces.jsonl
    bounces_file = VAULT / "operations" / "email-bounces.jsonl"
    if bounces_file.exists():
        for line in bounces_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                e = d.get("email") or d.get("to") or d.get("address") or ""
                if e:
                    suppressed.add(e.lower().strip())
            except Exception:
                pass

    # suppression.txt
    supp_file = VAULT / "mailbox" / "suppression.txt"
    if supp_file.exists():
        for line in supp_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            email = line.split()[0].lower()
            suppressed.add(email)

    # existing wf_ files
    for wf in LEADS_DIR.glob("wf_*.json"):
        try:
            d = json.loads(wf.read_text())
            e = d.get("lead_email", "")
            if e:
                suppressed.add(e.lower().strip())
        except Exception:
            pass

    # Day-0 leads already firing (don't double-queue)
    for day0 in ["arjun@rtrvr.ai", "riley@charlielabs.ai", "hello@octokraft.com"]:
        suppressed.add(day0)

    return suppressed


# ── Lead sources ─────────────────────────────────────────────────────────────

def load_existing_founder_leads() -> list[dict[str, Any]]:
    """Load from the founder-specific JSONL files."""
    files = [
        VAULT / "projects" / "outreach" / "founder-leads-2026-04-21.jsonl",
        VAULT / "projects" / "outreach" / "leads-founders-2026-04-21.jsonl",
        VAULT / "projects" / "outreach" / "warm-pipeline.jsonl",
    ]
    leads = []
    for f in files:
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                email = (
                    d.get("email") or d.get("contact") or d.get("lead_email") or ""
                ).strip()
                if not email or "@" not in email:
                    continue
                leads.append({
                    "email": email,
                    "name": d.get("name") or d.get("lead_name") or "",
                    "company": d.get("company") or d.get("product") or "",
                    "domain": (d.get("website") or d.get("product") or email.split("@")[-1]).strip("/"),
                    "context": d.get("context") or d.get("bio") or "",
                    "source": d.get("source") or f.name,
                    "homepage_url": d.get("website") or d.get("homepage_url") or "",
                })
            except Exception:
                pass
    return leads


def _gh_fetch(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if gh_token:
        req.add_header("Authorization", f"Bearer {gh_token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def fetch_github_trending() -> list[dict[str, Any]]:
    """Search GitHub for active AI/dev-tools/SaaS repos pushed in last 30 days."""
    print("  → GitHub: searching AI/dev-tools/SaaS repos (last 30 days)...")
    queries = [
        "topic:ai-agent language:python pushed:>2026-04-01 stars:>10",
        "topic:developer-tools language:typescript pushed:>2026-04-01 stars:>20",
        "topic:saas language:python pushed:>2026-04-01 stars:>15",
        "topic:mcp-server pushed:>2026-04-01 stars:>5",
        "topic:indie-hacker pushed:>2026-04-01 stars:>5",
        "topic:bootstrapped pushed:>2026-04-01 stars:>5",
        "topic:cli-tool language:go pushed:>2026-04-01 stars:>20",
        "ai workflow automation pushed:>2026-04-01 stars:>30 language:python",
    ]
    leads: list[dict[str, Any]] = []
    seen_owners: set[str] = set()
    for q in queries:
        try:
            url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(q)}&sort=stars&order=desc&per_page=10"
            data = _gh_fetch(url)
            for item in data.get("items", []):
                owner = item.get("owner", {})
                login = owner.get("login", "")
                if not login or login in seen_owners:
                    continue
                seen_owners.add(login)
                # Try to get user email
                try:
                    user_data = _gh_fetch(f"https://api.github.com/users/{login}")
                    email = (user_data.get("email") or "").strip()
                    blog = user_data.get("blog") or ""
                    name = user_data.get("name") or login
                    bio = user_data.get("bio") or ""
                    company = user_data.get("company") or ""
                    location = user_data.get("location") or ""
                except Exception:
                    email = ""
                    blog = ""
                    name = login
                    bio = ""
                    company = ""
                    location = ""

                if not email or "@" not in email:
                    # Skip if no public email
                    continue

                domain = ""
                if blog:
                    blog_clean = blog.strip().rstrip("/")
                    if blog_clean and not blog_clean.startswith("http"):
                        blog_clean = "https://" + blog_clean
                    domain = blog_clean

                context = (
                    f"{item.get('description', '')}. "
                    f"Repo: {item.get('full_name')} ({item.get('stargazers_count', 0)} stars). "
                    f"Bio: {bio}. Company: {company}. Location: {location}."
                ).strip()

                leads.append({
                    "email": email,
                    "name": name,
                    "company": company or item.get("name", ""),
                    "domain": domain or email.split("@")[-1],
                    "context": context,
                    "source": "github-trending",
                    "homepage_url": domain or "",
                    "github_repo": item.get("html_url", ""),
                    "stars": item.get("stargazers_count", 0),
                })
            time.sleep(0.5)
        except Exception as e:
            print(f"    GitHub query failed: {q[:50]}... → {e}")
    print(f"  → GitHub: found {len(leads)} leads with public emails")
    return leads


def fetch_producthunt_recent() -> list[dict[str, Any]]:
    """Fetch recent PH launches via PH API v2 or HTML fallback."""
    print("  → ProductHunt: fetching recent launches...")
    leads: list[dict[str, Any]] = []

    ph_token = os.environ.get("PRODUCTHUNT_API_TOKEN") or os.environ.get("PH_API_TOKEN") or ""
    if ph_token:
        # GraphQL query for recent posts
        gql = """
        {
          posts(first: 30, order: VOTES, postedAfter: "2026-04-01T00:00:00Z") {
            edges {
              node {
                id name tagline website votesCount
                makers { edges { node { name username profileImage websiteUrl twitterUsername } } }
              }
            }
          }
        }
        """
        try:
            payload = json.dumps({"query": gql}).encode()
            req = urllib.request.Request(
                "https://api.producthunt.com/v2/api/graphql",
                data=payload,
                headers={
                    "Authorization": f"Bearer {ph_token}",
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
            for edge in resp.get("data", {}).get("posts", {}).get("edges", []):
                node = edge.get("node", {})
                product_name = node.get("name", "")
                tagline = node.get("tagline", "")
                website = node.get("website", "")
                votes = node.get("votesCount", 0)
                for maker_edge in node.get("makers", {}).get("edges", []):
                    maker = maker_edge.get("node", {})
                    maker_name = maker.get("name", "")
                    maker_site = maker.get("websiteUrl", "") or website
                    context = f"{product_name}: {tagline}. {votes} upvotes on Product Hunt."
                    domain = (maker_site or website or "").strip().rstrip("/")
                    # Only include if we can find an email later via domain
                    if domain:
                        leads.append({
                            "email": "",  # will attempt discovery below
                            "name": maker_name,
                            "company": product_name,
                            "domain": domain,
                            "context": context,
                            "source": "producthunt-api",
                            "homepage_url": domain,
                            "ph_votes": votes,
                        })
            print(f"  → ProductHunt API: found {len(leads)} makers")
        except Exception as e:
            print(f"  → ProductHunt API failed: {e}")
    else:
        print("  → No PH API token, skipping PH GraphQL")

    return leads


def fetch_hn_show_hn() -> list[dict[str, Any]]:
    """Fetch HN Show HN posts from last 30 days via Algolia API."""
    print("  → HackerNews: fetching Show HN posts (last 30 days)...")
    leads: list[dict[str, Any]] = []
    try:
        url = (
            "https://hn.algolia.com/api/v1/search?"
            "tags=show_hn"
            "&numericFilters=created_at_i%3E1743465600"  # ~April 1 2026
            "&hitsPerPage=100"
            "&query=saas+OR+tool+OR+agent+OR+api+OR+founder+OR+indie"
        )
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        for hit in data.get("hits", []):
            title = hit.get("title", "")
            author = hit.get("author", "")
            url_field = hit.get("url", "")
            points = hit.get("points", 0)
            hn_id = hit.get("objectID", "")

            if not url_field or not author:
                continue
            if points < 20:
                continue

            parsed = urllib.parse.urlparse(url_field)
            domain = parsed.netloc.replace("www.", "")
            if not domain:
                continue

            # Skip local-SMB
            if _is_local_smb(title):
                continue

            # Try common email patterns
            email_candidates = [
                f"hello@{domain}",
                f"hi@{domain}",
                f"contact@{domain}",
            ]

            context = f"Show HN: {title}. {points} points. HN user: {author}. URL: {url_field}"
            leads.append({
                "email": email_candidates[0],
                "email_candidates": email_candidates,
                "name": author,
                "company": domain,
                "domain": url_field,
                "context": context,
                "source": "hn-show-hn",
                "homepage_url": url_field,
                "hn_points": points,
                "hn_id": hn_id,
            })

        print(f"  → HN Show HN: found {len(leads)} qualifying posts")
    except Exception as e:
        print(f"  → HN Show HN failed: {e}")
    return leads


# ── Scoring ──────────────────────────────────────────────────────────────────

def heuristic_pre_filter(lead: dict[str, Any]) -> bool:
    """Quick heuristic to skip obvious non-ICP before expensive LLM calls."""
    context = (lead.get("context") or "").lower()
    domain = (lead.get("domain") or "").lower()
    email = (lead.get("email") or "").lower()

    if _is_local_smb(context + " " + domain):
        return False
    if any(x in email for x in ["noreply", "no-reply", "info@", "support@", "admin@", "hello@"]):
        # These generic emails are ok for cold outreach but flag for scoring
        pass
    tld = domain.split(".")[-1].split("/")[0] if "." in domain else ""
    if tld in ("gov", "edu", "mil"):
        return False
    return True


def score_lead_batch(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score each lead using founder-icp-scorer.py batch mode."""
    print(f"  → Scoring {len(leads)} leads via opus-4-7 (route=review)...")
    # Write to temp JSONL
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False, encoding="utf-8")
    for lead in leads:
        tmp.write(json.dumps({
            "email": lead.get("email", ""),
            "domain": lead.get("domain", ""),
            "homepage_url": lead.get("homepage_url", ""),
            "context": lead.get("context", ""),
            "name": lead.get("name", ""),
            "company": lead.get("company", ""),
            "source": lead.get("source", ""),
        }, ensure_ascii=False) + "\n")
    tmp.close()
    tmp_path = Path(tmp.name)

    # Run scorer
    import subprocess
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "founder-icp-scorer.py"),
         "--input-file", str(tmp_path), "--jsonl"],
        capture_output=True, text=True, cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    tmp_path.unlink(missing_ok=True)

    scored = []
    if result.returncode != 0:
        print(f"  ⚠ scorer stderr: {result.stderr[:500]}")

    # Parse output
    for i, line in enumerate(result.stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            s = json.loads(line)
            # Merge original lead data
            original = leads[i] if i < len(leads) else {}
            merged = {**original, **s}
            scored.append(merged)
        except Exception:
            if i < len(leads):
                scored.append({**leads[i], "score": 0.0, "label": "not-icp", "reasoning": "parse-error"})

    print(f"  → Scored {len(scored)} leads")
    return scored


# ── Opener generation ─────────────────────────────────────────────────────────

def generate_opener(lead: dict[str, Any]) -> dict[str, str]:
    """Generate personalized cold-email subject + opener via opus-4-7."""
    name = lead.get("name") or lead.get("company") or "Founder"
    company = lead.get("company") or lead.get("domain") or ""
    context = lead.get("context") or ""
    positive_signals = lead.get("positive_signals") or []
    homepage_excerpt = (lead.get("homepage_excerpt") or "")[:1500]
    score = lead.get("score", 0)
    reasoning = lead.get("reasoning") or ""

    prompt = f"""You are Rick — an autonomous AI CEO. You write cold email openers that are SPECIFIC, sharp, warm, and never generic.

TARGET FOUNDER:
Name: {name}
Company: {company}
Context: {context}
ICP signals: {', '.join(positive_signals[:6])}
Reasoning: {reasoning}
Homepage excerpt: {homepage_excerpt[:800]}

RULES:
- Subject: 8 words max, specific to their product/situation (never generic like "Quick question" or "AI CEO for you")
- Body: 4-6 sentences MAX. Lead with ONE specific thing you noticed about their product/work.
- Reference their actual product name, technology, or a specific signal.
- End with a soft CTA: "Worth a quick look?" or similar low-friction line.
- Sign as: Rick | meetrick.ai
- Tone: sharp, warm, founder-to-founder. No hype, no corporate speak.
- NEVER say "autonomous AI CEO" in the opener — show, don't tell.
- DO NOT mention "I'm an AI" unless it's clearly ironic and earns a laugh.

Return JSON only:
{{"subject": "...", "body": "..."}}"""

    fallback_subj = f"{company} — quick thought"
    fallback_body = f"Hi {name.split()[0] if name else 'there'},\n\nBuilding {company} looks like exactly the kind of project where having autonomous ops backing you up would compound fast.\n\nRick handles the CEO layer — outreach, growth experiments, ops decisions — so the founder stays on product.\n\nWorth a quick look?\n\nRick | meetrick.ai"
    fallback = json.dumps({"subject": fallback_subj, "body": fallback_body})

    result = generate_text("review", prompt, fallback)
    raw = getattr(result, "content", str(result))

    # Parse JSON
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        parsed = json.loads(cleaned)
        return {
            "subject": str(parsed.get("subject", fallback_subj))[:120],
            "body": str(parsed.get("body", fallback_body))[:3000],
            "model_used": getattr(result, "model", "claude-opus-4-7"),
        }
    except Exception:
        return {"subject": fallback_subj, "body": fallback_body, "model_used": "fallback"}


# ── Workflow creation ─────────────────────────────────────────────────────────

def make_wf_id(email: str) -> str:
    h = hashlib.md5(email.lower().encode()).hexdigest()[:12]
    return f"wf_{h}"


def create_workflow(lead: dict[str, Any], opener: dict[str, str]) -> dict[str, Any]:
    email = lead.get("email", "")
    wf_id = make_wf_id(email)
    wf_path = LEADS_DIR / f"{wf_id}.json"

    wf = {
        "workflow_id": wf_id,
        "workflow_type": "qualified_lead",
        "stage": "cold-email-pending",
        "status": "active",
        "lead_email": email,
        "lead_name": lead.get("name") or "",
        "company": lead.get("company") or "",
        "domain": lead.get("domain") or "",
        "source": lead.get("source") or "founder-discovery",
        "icp_score": lead.get("score", 0),
        "icp_label": lead.get("label", "icp"),
        "icp_reasoning": lead.get("reasoning", ""),
        "icp_signals": lead.get("positive_signals", []),
        "context": lead.get("context", ""),
        "homepage_url": lead.get("homepage_url", ""),
        "subject": opener["subject"],
        "body": opener["body"],
        "generated_at": NOW_UTC,
        "model_used": opener.get("model_used", "claude-opus-4-7"),
        "auto_fire": False,
        "draft": True,
        "do_not_send_before_approval": True,
    }

    if not wf_path.exists():
        wf_path.write_text(json.dumps(wf, indent=2, ensure_ascii=False))
        return wf
    else:
        # Already exists — don't overwrite
        return {}


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--min-score", type=float, default=0.65)
    args = ap.parse_args()

    print(f"\n{'='*60}")
    print("FOUNDER ICP DISCOVERY PIPELINE")
    print(f"Target: {args.limit} leads, min score {args.min_score}")
    print(f"{'='*60}\n")

    # 1. Load suppression set
    suppressed = load_suppression_set()
    print(f"[1/6] Suppression set: {len(suppressed)} entries")

    # 2. Gather candidates
    print("[2/6] Gathering candidates from all sources...")
    all_candidates: list[dict[str, Any]] = []

    # Existing founder lead files
    existing = load_existing_founder_leads()
    print(f"  → Existing founder files: {len(existing)} leads")
    all_candidates.extend(existing)

    # GitHub trending
    gh_leads = fetch_github_trending()
    all_candidates.extend(gh_leads)

    # ProductHunt
    ph_leads = fetch_producthunt_recent()
    all_candidates.extend(ph_leads)

    # HN Show HN
    hn_leads = fetch_hn_show_hn()
    all_candidates.extend(hn_leads)

    print(f"  → Total raw candidates: {len(all_candidates)}")

    # 3. Deduplicate + filter suppressed + heuristic pre-filter
    print("[3/6] Deduplicating and filtering...")
    seen_emails: set[str] = set()
    filtered: list[dict[str, Any]] = []
    skip_counts = {"suppressed": 0, "no_email": 0, "dup": 0, "smb": 0, "heuristic": 0}

    for lead in all_candidates:
        email = (lead.get("email") or "").strip().lower()
        if not email or "@" not in email:
            skip_counts["no_email"] += 1
            continue
        if email in suppressed:
            skip_counts["suppressed"] += 1
            continue
        if email in seen_emails:
            skip_counts["dup"] += 1
            continue
        context_combined = (lead.get("context") or "") + " " + (lead.get("company") or "") + " " + (lead.get("domain") or "")
        if _is_local_smb(context_combined):
            skip_counts["smb"] += 1
            continue
        if not heuristic_pre_filter(lead):
            skip_counts["heuristic"] += 1
            continue
        seen_emails.add(email)
        filtered.append(lead)

    print(f"  → After filter: {len(filtered)} candidates")
    print(f"  → Skipped: {skip_counts}")

    if not filtered:
        print("  ⚠ No candidates after filtering — check data sources")
        return

    # 4. Score all via opus-4-7
    print(f"[4/6] Scoring {len(filtered)} candidates via opus-4-7 (route=review)...")
    scored = score_lead_batch(filtered)

    # Sort by score descending
    scored.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Filter by min-score and pick top N
    qualified = [s for s in scored if s.get("score", 0) >= args.min_score]
    print(f"  → {len(qualified)} candidates score ≥ {args.min_score}")

    top = qualified[:args.limit]
    print(f"  → Taking top {len(top)}")

    if not top:
        print("\n⚠ No qualified leads found. Try lowering --min-score.")
        # Show top 10 regardless
        print("\nTop 10 scored (below threshold):")
        for s in scored[:10]:
            print(f"  {s.get('score', 0):.2f} | {s.get('email', '?')} | {s.get('company', '?')}")
        return

    # Score distribution
    scores = [s.get("score", 0) for s in top]
    print(f"\nScore distribution: min={min(scores):.2f} avg={sum(scores)/len(scores):.2f} max={max(scores):.2f}")

    # 5. Generate openers + create workflows
    print(f"\n[5/6] Generating opus-4-7 personalized openers for {len(top)} leads...")
    created = []
    skipped_existing = []

    for i, lead in enumerate(top):
        email = lead.get("email", "")
        company = lead.get("company", email)
        score = lead.get("score", 0)
        print(f"  [{i+1}/{len(top)}] {score:.2f} | {email} | {company[:40]}")

        if args.dry_run:
            print(f"    [DRY RUN] Would generate opener + create wf_{make_wf_id(email)}.json")
            continue

        opener = generate_opener(lead)
        result = create_workflow(lead, opener)
        if result:
            created.append(result)
        else:
            skipped_existing.append(email)
        time.sleep(0.3)  # Rate-limit courtesy

    # 6. Report
    print(f"\n[6/6] RESULTS")
    print(f"{'='*60}")
    print(f"Total candidates scored:   {len(scored)}")
    print(f"Qualified (≥{args.min_score}):         {len(qualified)}")
    print(f"Target batch (top {args.limit}):      {len(top)}")
    print(f"New wf_ files created:     {len(created)}")
    print(f"Skipped (already exists):  {len(skipped_existing)}")

    if created:
        print(f"\nScore distribution of created leads:")
        created_scores = [c.get("icp_score", 0) for c in created]
        bands = {"0.90+": 0, "0.80-0.89": 0, "0.70-0.79": 0, "0.65-0.69": 0}
        for s in created_scores:
            if s >= 0.90:
                bands["0.90+"] += 1
            elif s >= 0.80:
                bands["0.80-0.89"] += 1
            elif s >= 0.70:
                bands["0.70-0.79"] += 1
            else:
                bands["0.65-0.69"] += 1
        for band, count in bands.items():
            print(f"  {band}: {count}")

        print(f"\nTop 5 leads (dossier quality):")
        for wf in created[:5]:
            print(f"\n  ── {wf['lead_email']} ──")
            print(f"  Company:  {wf['company']}")
            print(f"  ICP:      {wf['icp_score']:.2f} | {wf['icp_label']}")
            print(f"  Signals:  {', '.join(wf['icp_signals'][:4])}")
            print(f"  Source:   {wf['source']}")
            print(f"  Subject:  {wf['subject']}")
            body_preview = wf['body'][:200].replace('\n', ' ')
            print(f"  Opener:   {body_preview}...")
            print(f"  Workflow: {wf['workflow_id']} | stage={wf['stage']} | auto_fire={wf['auto_fire']}")

        print(f"\nAll lead emails:")
        for wf in created:
            print(f"  {wf['icp_score']:.2f} | {wf['lead_email']} | {wf['company']} | {wf['workflow_id']}")


if __name__ == "__main__":
    main()
