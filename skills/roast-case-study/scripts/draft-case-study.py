#!/usr/bin/env python3
"""Strategy C #2 — Roast case-study drafter.

Turns a roast capture into a LinkedIn-shaped before/after card that Vlad
pastes to his personal feed. Pure drafter — never auto-publishes.

CLI:
  python3 draft-case-study.py --domain virtueofvague.com \
      --roast-summary "vague hero, missing CTA, no proof" [--dry-run]
  python3 draft-case-study.py --lead-id rl_xxx [--dry-run]

Output:
  ~/rick-vault/mailbox/drafts/case-study/<YYYY-MM-DD>-<sanitized-domain>.md

Telegram alert:
  ~/clawd/scripts/tg-topic.sh customer "📝 Roast case-study draft: <domain> ..."

Safety:
  - DRY-RUN by default (no file write, no Telegram).
  - Live only when --dry-run is omitted AND RICK_ROAST_CASE_STUDY_LIVE=1.
  - Never references the prospect's email (domain only).
  - Never claims metrics Rick doesn't have ($9 MRR / 1 customer is the truth).
  - Always ends the card with the /roast CTA.
  - All errors graceful no-op + log; main() never raises.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# Make `runtime` importable when called from cron / launchd / Vlad's shell.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DRAFTS_DIR = DATA_ROOT / "mailbox" / "drafts" / "case-study"
LOG_FILE = DATA_ROOT / "operations" / "roast-case-study.jsonl"
LEAD_POLL_LOG = DATA_ROOT / "operations" / "roast-lead-poll.jsonl"

API_BASE = os.getenv("MEETRICK_API_BASE", "https://api.meetrick.ai")
TG_TOPIC_SH = Path.home() / "clawd" / "scripts" / "tg-topic.sh"
USER_AGENT = "Rick-RoastCaseStudy/1.0"
DEFAULT_TIMEOUT = 12
MAX_CARD_CHARS = 2500


# ---------------------------------------------------------------------------
# I/O helpers (defensive — never raise)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _log(event: str, **fields: Any) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": _now_iso(), "event": event, **fields}
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _sanitize_domain(domain: str) -> str:
    s = (domain or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/", 1)[0]
    s = re.sub(r"[^a-z0-9.-]", "-", s)
    return s[:80] or "unknown"


def _domain_for_filename(domain: str) -> str:
    s = _sanitize_domain(domain)
    return s.replace(".", "-")


# ---------------------------------------------------------------------------
# Lead lookup (local cache OR meetrick-api)
# ---------------------------------------------------------------------------

def _load_lead_from_local_log(lead_id: str) -> dict | None:
    """Scan the roast-lead-poll log for the most recent entry matching lead_id.

    The poll log already records (lead_id, email, domain, source) per dispatch
    line. We only need (domain, roast_summary). roast_summary isn't in the log,
    so we still fall back to the API for that — but having domain locally
    saves a round-trip when the API is unreachable.
    """
    if not lead_id or not LEAD_POLL_LOG.is_file():
        return None
    try:
        # Walk backwards through the file (small enough — JSONL).
        domain = ""
        with LEAD_POLL_LOG.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("lead_id") == lead_id:
                domain = (rec.get("domain") or "").strip()
                if domain:
                    return {"id": lead_id, "domain": domain, "roast_summary": ""}
        return None
    except Exception:
        return None


def _fetch_lead_from_api(lead_id: str) -> dict | None:
    """GET /api/v1/roast-leads/recent and find lead_id in the response.

    Returns the matching lead dict or None. Never raises.
    """
    secret = os.getenv("ROAST_INGEST_SECRET", "").strip()
    if not secret:
        _log("api.skip.no_secret", lead_id=lead_id)
        return None
    qs = urllib.parse.urlencode({"since_id": "", "limit": "100"})
    url = f"{API_BASE}/api/v1/roast-leads/recent?{qs}"
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "X-Worker-Secret": secret,
    }
    try:
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                _log("api.bad_json", lead_id=lead_id, raw=raw[:200])
                return None
        if not data.get("ok"):
            _log("api.not_ok", lead_id=lead_id, error=str(data.get("error"))[:200])
            return None
        for lead in (data.get("leads") or []):
            if (lead.get("id") or "").strip() == lead_id:
                return lead
        _log("api.lead_not_found", lead_id=lead_id, returned=len(data.get("leads") or []))
        return None
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        _log("api.http_error", lead_id=lead_id, status=e.code, body=body)
        return None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        _log("api.network_error", lead_id=lead_id, error=str(e)[:200])
        return None
    except Exception as e:
        _log("api.unexpected_error", lead_id=lead_id, error=str(e)[:200])
        return None


def _resolve_lead(lead_id: str) -> tuple[str, str]:
    """Returns (domain, roast_summary). Either may be empty on failure."""
    api_lead = _fetch_lead_from_api(lead_id)
    if api_lead:
        domain = (api_lead.get("domain") or "").strip()
        summary = (api_lead.get("roast_summary") or "").strip()
        if domain or summary:
            return domain, summary
    local = _load_lead_from_local_log(lead_id)
    if local:
        return local.get("domain", ""), local.get("roast_summary", "")
    return "", ""


# ---------------------------------------------------------------------------
# Drafting (LLM via runtime.llm.generate_text)
# ---------------------------------------------------------------------------

def _build_prompt(domain: str, roast_summary: str) -> str:
    safe_domain = _sanitize_domain(domain)
    summary_block = (roast_summary or "(no detailed summary provided — infer 3 plausible specific issues from the domain alone)")[:1200]
    return (
        "You are Rick — autonomous AI CEO at meetrick.ai. Vlad (the founder) "
        "is going to paste your output to his LinkedIn personal feed. So write "
        "this in HIS voice: founder-direct, dry humor, opinion-first, no buzzwords.\n\n"
        f"You just roasted the landing page at {safe_domain}.\n\n"
        f"Roast notes:\n{summary_block}\n\n"
        "Draft a LinkedIn case-study card in EXACTLY this format (≤2500 chars):\n\n"
        f"🔥 Just roasted: {safe_domain}\n\n"
        "What I saw (60s scan):\n"
        "- <pain point 1 — specific, references something real on the page>\n"
        "- <pain point 2 — specific>\n"
        "- <pain point 3 — specific>\n\n"
        "How I'd fix it:\n"
        "1. <fix 1 — concrete action, not generic 'improve your CTA'>\n"
        "2. <fix 2 — concrete>\n"
        "3. <fix 3 — concrete>\n\n"
        "The honest version: most landing pages have the same 3 problems. Mine\n"
        "too. The difference is whether the founder knows.\n\n"
        "Want yours? https://meetrick.ai/roast — free, 60s, no email gate.\n\n"
        "— Rick (autonomous AI CEO @ meetrick.ai)\n\n"
        "HARD RULES:\n"
        "- NEVER mention or reference the prospect's email address.\n"
        "- NEVER claim metrics Rick doesn't have. Real MRR is $9 / 1 paying customer (Newton).\n"
        "- The pain points and fixes must be SPECIFIC to what the roast notes describe — not template boilerplate.\n"
        "- Keep the closing CTA line and signature EXACTLY as shown above.\n"
        "- Output: just the card text. No frontmatter, no markdown code fences, no commentary.\n"
    )


def _build_fallback(domain: str, roast_summary: str) -> str:
    safe_domain = _sanitize_domain(domain)
    notes = (roast_summary or "vague hero, missing CTA, no proof").strip()
    # Try to split the summary into 3 plausible issues for the bullet list.
    parts = [p.strip(" -•") for p in re.split(r"[,;.\n]", notes) if p.strip(" -•")]
    parts = (parts + ["unclear value prop", "no social proof", "weak primary CTA"])[:3]
    fixes = [
        f"rewrite the hero to lead with the outcome, not the feature",
        f"add a single line of proof above the fold (logo, number, quote — pick one)",
        f"make the primary CTA do ONE thing and put it in two places",
    ]
    return (
        f"🔥 Just roasted: {safe_domain}\n\n"
        "What I saw (60s scan):\n"
        f"- {parts[0]}\n"
        f"- {parts[1]}\n"
        f"- {parts[2]}\n\n"
        "How I'd fix it:\n"
        f"1. {fixes[0]}\n"
        f"2. {fixes[1]}\n"
        f"3. {fixes[2]}\n\n"
        "The honest version: most landing pages have the same 3 problems. Mine\n"
        "too. The difference is whether the founder knows.\n\n"
        "Want yours? https://meetrick.ai/roast — free, 60s, no email gate.\n\n"
        "— Rick (autonomous AI CEO @ meetrick.ai)"
    )


def _scrub_card(card: str, domain: str) -> str:
    """Strip anything that violates the safety rules.

    - Remove any email-looking strings (defense-in-depth — prompt also forbids).
    - Cap at MAX_CARD_CHARS.
    - Ensure the /roast CTA is present; append fallback CTA if missing.
    """
    if not card:
        return card
    # Strip email addresses (any @-something).
    card = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[redacted]", card)
    # Strip wrapping markdown code fences if the model added them.
    card = re.sub(r"^```[a-zA-Z]*\n", "", card.strip())
    card = re.sub(r"\n```$", "", card.strip())
    card = card.strip()
    if "meetrick.ai/roast" not in card:
        card += "\n\nWant yours? https://meetrick.ai/roast — free, 60s, no email gate.\n\n— Rick (autonomous AI CEO @ meetrick.ai)"
    if len(card) > MAX_CARD_CHARS:
        card = card[: MAX_CARD_CHARS - 1].rstrip() + "…"
    return card


def _generate_card(domain: str, roast_summary: str) -> tuple[str, dict]:
    prompt = _build_prompt(domain, roast_summary)
    fallback = _build_fallback(domain, roast_summary)
    try:
        from runtime.llm import generate_text  # noqa: WPS433
        result = generate_text("writing", prompt, fallback)
        body = (result.content if hasattr(result, "content") else str(result)).strip()
        meta = {
            "model": getattr(result, "model_used", "claude-sonnet-4-6"),
            "fallback_used": False,
        }
    except Exception as e:
        body = fallback
        meta = {"model": "fallback", "fallback_used": True, "error": str(e)[:200]}
    return _scrub_card(body, domain), meta


# ---------------------------------------------------------------------------
# Output writers (file + telegram)
# ---------------------------------------------------------------------------

def _write_draft_file(domain: str, card: str, lead_id: str | None) -> Path:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    today = _today_stamp()
    fname = f"{today}-{_domain_for_filename(domain)}.md"
    path = DRAFTS_DIR / fname
    # If a draft already exists for today+domain, append a suffix so we don't clobber.
    if path.exists():
        suffix = 2
        while True:
            alt = DRAFTS_DIR / f"{today}-{_domain_for_filename(domain)}-{suffix}.md"
            if not alt.exists():
                path = alt
                break
            suffix += 1
    frontmatter_lines = [
        "---",
        "kind: roast-case-study",
        "target_channel: linkedin_personal",
        "draft: true",
        "review_required: true",
        f"domain: {_sanitize_domain(domain)}",
        f"created_at: {_now_iso()}",
    ]
    if lead_id:
        frontmatter_lines.append(f"lead_id: {lead_id}")
    frontmatter_lines.append("---")
    body = "\n".join(frontmatter_lines) + "\n\n" + card.rstrip() + "\n"
    path.write_text(body, encoding="utf-8")
    return path


def _post_telegram(domain: str) -> tuple[bool, str]:
    """Best-effort Telegram alert via tg-topic.sh customer."""
    if not TG_TOPIC_SH.is_file():
        return False, "tg-topic.sh missing"
    msg = f"📝 Roast case-study draft: {_sanitize_domain(domain)} — review at /draft N"
    try:
        result = subprocess.run(
            ["bash", str(TG_TOPIC_SH), "customer", msg],
            capture_output=True,
            text=True,
            timeout=20,
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        ok = result.returncode == 0 and ("OK" in out or not err)
        return ok, out or err or f"rc={result.returncode}"
    except subprocess.TimeoutExpired:
        return False, "tg-topic.sh timeout"
    except Exception as e:
        return False, f"tg-topic.sh exception: {str(e)[:160]}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Roast case-study drafter (Strategy C #2)")
    ap.add_argument("--domain", default=None, help="Roasted domain (e.g. virtueofvague.com)")
    ap.add_argument("--roast-summary", default=None, help="Free-text summary of pain points found")
    ap.add_argument("--lead-id", default=None, help="rl_xxx — load from local log + meetrick-api")
    ap.add_argument("--dry-run", action="store_true", help="Print card to stdout, write nothing, send no Telegram")
    args = ap.parse_args()

    summary: dict[str, Any] = {"ts": _now_iso(), "args": {
        "domain": args.domain, "lead_id": args.lead_id, "dry_run": args.dry_run,
    }}

    domain = (args.domain or "").strip()
    roast_summary = (args.roast_summary or "").strip()
    lead_id = (args.lead_id or "").strip() or None

    # Resolve lead if --lead-id is given.
    if lead_id and (not domain or not roast_summary):
        try:
            looked_domain, looked_summary = _resolve_lead(lead_id)
        except Exception as e:
            looked_domain, looked_summary = "", ""
            _log("resolve.crash", lead_id=lead_id, error=str(e)[:200])
        if not domain:
            domain = looked_domain
        if not roast_summary:
            roast_summary = looked_summary

    if not domain:
        msg = "missing domain (provide --domain or --lead-id resolvable to a domain)"
        _log("input.missing_domain", lead_id=lead_id)
        print(json.dumps({"status": "error", "error": msg}))
        return 0  # graceful no-op exit code

    # Draft (LLM with fallback).
    try:
        card, gen_meta = _generate_card(domain, roast_summary)
    except Exception as e:
        _log("generate.crash", domain=domain, error=str(e)[:200])
        card = _build_fallback(domain, roast_summary)
        gen_meta = {"model": "fallback", "fallback_used": True, "error": str(e)[:200]}

    summary.update({
        "domain": _sanitize_domain(domain),
        "lead_id": lead_id,
        "card_chars": len(card),
        "generation": gen_meta,
        "card_preview": card[:280],
    })

    # Decide live vs. dry-run. CLI --dry-run always wins; otherwise require env flag.
    env_live = os.getenv("RICK_ROAST_CASE_STUDY_LIVE", "0").strip().lower() in ("1", "true", "yes")
    live = env_live and not args.dry_run

    if not live:
        summary["status"] = "dry-run"
        summary["reason"] = "cli --dry-run" if args.dry_run else "RICK_ROAST_CASE_STUDY_LIVE != 1"
        print(json.dumps(summary, indent=2))
        print("\n--- CARD PREVIEW ---\n" + card + "\n--- END ---")
        _log("run.dry_run", **{k: v for k, v in summary.items() if k != "card_preview"})
        return 0

    # Live: write the file + post the Telegram alert.
    try:
        path = _write_draft_file(domain, card, lead_id)
        summary["draft_path"] = str(path)
    except Exception as e:
        _log("write.crash", domain=domain, error=str(e)[:200])
        summary["status"] = "error"
        summary["error"] = f"write_failed: {str(e)[:160]}"
        print(json.dumps(summary, indent=2))
        return 0

    tg_ok, tg_info = _post_telegram(domain)
    summary["telegram"] = {"ok": tg_ok, "info": tg_info[:200]}
    summary["status"] = "drafted"

    print(json.dumps(summary, indent=2))
    _log("run.live", **{k: v for k, v in summary.items() if k != "card_preview"})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        # Last-resort catch — never crash launchd / cron.
        _log("run.crash", error=str(e)[:300])
        print(json.dumps({"status": "fatal", "error": str(e)[:300]}), file=sys.stderr)
        raise SystemExit(0)
