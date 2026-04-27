#!/usr/bin/env python3
"""Founder ICP scorer for Rick.

Scores whether a lead looks like a technical solo-founder / indie-hacker /
bootstrapped SaaS founder who would actually install Rick.

Inputs:
  - email
  - domain
  - homepage text (or a homepage URL that will be fetched)

Primary gate:
  route='review' → claude-opus-4-7 via runtime.llm.generate_text

Examples:
  python3 scripts/founder-icp-scorer.py --email hi@acme.dev --domain acme.dev --homepage-url https://acme.dev
  python3 scripts/founder-icp-scorer.py --input-file leads.jsonl --jsonl
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.llm import generate_text  # noqa: E402

USER_AGENT = "Rick-FounderICP/1.0 (+https://meetrick.ai)"
MAX_HOME_TEXT_CHARS = 12000
MAX_PROMPT_HOME_CHARS = 5000
ENV_CANDIDATES = [
    Path(os.getenv("RICK_ENV_FILE", str(Path.home() / "clawd" / "config" / "rick.env"))),
    ROOT / "config" / "rick.env",
]


def _load_env_files() -> None:
    for candidate in ENV_CANDIDATES:
        if not candidate.exists():
            continue
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:]
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        except OSError:
            continue


POSITIVE_TERMS = {
    "founder", "cofounder", "co-founder", "indie hacker", "indiehackers", "bootstrapped",
    "building in public", "building public", "shipping", "shipped", "ship fast", "launch",
    "launched", "mrr", "arr", "revenue", "side project", "solo founder", "solo builder",
    "maker", "developer", "devtools", "open source", "github", "api", "sdk", "docs",
    "changelog", "waitlist", "beta", "product hunt", "hn", "hacker news", "ship every day",
    "automation", "workflow", "agent", "mcp", "cli", "saas", "startup", "build", "built",
}
NEGATIVE_TERMS = {
    "book now", "appointments", "patients", "patient", "clinic", "chiropractic", "chiro",
    "restaurant", "restaurants", "menu", "reservation", "reservations", "wellness", "massage",
    "dental", "dentist", "law firm", "lawyer", "booking", "bookings", "front desk",
    "service business", "local business", "call now", "contact us", "near you", "locations",
}
TECH_TERMS = {
    "github", "gitlab", "api", "cli", "sdk", "open source", "repo", "repository",
    "webhook", "integration", "developer", "developers", "docs", "markdown", "mcp",
    "automation", "workflow", "agent", "ai agent", "indie hacker", "build in public",
    "ship", "shipped", "launch", "launching", "pricing", "changelog", "roadmap",
}
SOCIAL_TERMS = {
    "twitter.com", "x.com", "product hunt", "producthunt", "hn", "hacker news",
    "building in public", "#buildinpublic", "#indiehackers", "#startup", "#ship",
}


def _strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article|header|footer|tr|td|th)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_url(domain_or_url: str) -> list[str]:
    value = (domain_or_url or "").strip()
    if not value:
        return []
    if value.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(value)
        host = parsed.netloc or parsed.path
        root = f"{parsed.scheme}://{host}"
        return [root]
    host = value.split("/")[0].split("?")[0].strip()
    host = host.lstrip("www.")
    return [f"https://{host}", f"http://{host}", f"https://www.{host}", f"http://www.{host}"]


def fetch_homepage_text(domain_or_url: str) -> tuple[str, str]:
    """Fetch and clean homepage text. Returns (text, source_url)."""
    if not domain_or_url:
        return "", ""

    # If the caller already passed text, use it directly.
    if len(domain_or_url) > 300 and " " in domain_or_url and not domain_or_url.startswith(("http://", "https://")):
        return domain_or_url[:MAX_HOME_TEXT_CHARS], "provided-text"

    for candidate in _normalize_url(domain_or_url):
        try:
            req = urllib.request.Request(candidate, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            text = _strip_html(raw)
            if text:
                return text[:MAX_HOME_TEXT_CHARS], candidate
        except Exception:
            continue
    return "", ""


def _count_hits(text: str, terms: set[str]) -> list[str]:
    low = (text or "").lower()
    hits = []
    for term in sorted(terms, key=len, reverse=True):
        if term in low:
            hits.append(term)
    return hits


def heuristic_score(email: str, domain: str, homepage_text: str) -> dict[str, Any]:
    email = (email or "").lower().strip()
    domain = (domain or "").lower().strip()
    text = (homepage_text or "").lower()

    score = 0.12
    positives: list[str] = []
    negatives: list[str] = []

    # Domain/email shape
    if domain.endswith((".dev", ".io", ".ai", ".app", ".xyz", ".sh", ".tools")):
        score += 0.08
        positives.append(f"founder-ish domain TLD: {domain.rsplit('.', 1)[-1]}")
    if email and not any(bad in email for bad in ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com")):
        score += 0.04
        positives.append("company email, not generic inbox")

    # Text signals
    pos_hits = _count_hits(text, POSITIVE_TERMS)
    tech_hits = _count_hits(text, TECH_TERMS)
    social_hits = _count_hits(text, SOCIAL_TERMS)
    neg_hits = _count_hits(text, NEGATIVE_TERMS)

    score += min(0.28, 0.03 * len(pos_hits))
    score += min(0.18, 0.025 * len(tech_hits))
    score += min(0.10, 0.02 * len(social_hits))
    score -= min(0.45, 0.06 * len(neg_hits))

    positives.extend(pos_hits[:6])
    positives.extend(tech_hits[:5])
    positives.extend(social_hits[:3])
    negatives.extend(neg_hits[:6])

    # Sharp local-business downgrade.
    local_service_hits = [h for h in neg_hits if h in {"clinic", "chiropractic", "chiro", "patients", "booking", "bookings", "restaurant", "dentist", "massage", "wellness", "front desk"}]
    if local_service_hits:
        score -= 0.18
        negatives.extend(local_service_hits[:5])

    # Founder language boosts.
    if any(t in text for t in ("building in public", "indie hacker", "bootstrapped", "solo founder", "mrr", "ship", "launched")):
        score += 0.14
        positives.append("explicit founder/building language")

    score = max(0.0, min(1.0, score))
    label = "icp" if score >= 0.5 else "not-icp"
    return {
        "score": round(score, 3),
        "label": label,
        "confidence": round(min(0.98, 0.45 + abs(score - 0.5) * 0.9), 3),
        "positive_signals": list(dict.fromkeys(positives))[:10],
        "negative_signals": list(dict.fromkeys(negatives))[:10],
        "reasoning": (
            "Heuristic pass: "
            + ("strong founder/technical signals" if score >= 0.5 else "more like a service/business buyer than a founder buyer")
        ),
    }


def _parse_json_block(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # Prefer the first JSON object in the response.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        return json.loads(cleaned)
    except Exception:
        return None


def llm_score(email: str, domain: str, homepage_text: str, heuristic: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        "You are scoring ICP fit for Rick, an autonomous AI CEO product.\n"
        "Target buyer: technical solo-founders, indie hackers, bootstrapped SaaS founders, and founders who want autonomous ops.\n"
        "Wrong ICP: local service businesses, clinics, restaurants, booking-first businesses, and anyone mostly buying bookings instead of autonomous ops.\n\n"
        "Score 0.00 to 1.00.\n"
        "0.80-1.00 = exact ICP, obvious founder/operator.\n"
        "0.50-0.79 = likely ICP.\n"
        "0.20-0.49 = adjacent or unclear.\n"
        "0.00-0.19 = wrong ICP.\n\n"
        f"EMAIL: {email or ''}\n"
        f"DOMAIN: {domain or ''}\n"
        f"HEURISTIC_PREVIEW: {json.dumps(heuristic, ensure_ascii=False)}\n\n"
        "HOMEPAGE_TEXT:\n---\n"
        f"{homepage_text[:MAX_PROMPT_HOME_CHARS]}\n"
        "---\n\n"
        "Return JSON only with exactly these keys:\n"
        '{"score": 0.0, "label": "icp|not-icp", "confidence": 0.0, "reasoning": "...", "positive_signals": ["..."], "negative_signals": ["..."], "recommended_action": "keep|downgrade|cancel"}\n'
        "Keep reasoning terse and specific. Mention founder signals when present. If the lead is wrong-ICP, say so plainly."
    )
    fallback = json.dumps({
        **heuristic,
        "recommended_action": "keep" if heuristic["label"] == "icp" else "cancel",
    }, ensure_ascii=False)
    result = generate_text("review", prompt, fallback)
    raw = getattr(result, "content", str(result))
    parsed = _parse_json_block(raw) or _parse_json_block(fallback) or {}
    if not isinstance(parsed, dict):
        parsed = {}

    score = parsed.get("score", heuristic["score"])
    try:
        score = float(score)
    except Exception:
        score = float(heuristic["score"])
    score = max(0.0, min(1.0, score))

    positive_signals = parsed.get("positive_signals") or heuristic["positive_signals"]
    negative_signals = parsed.get("negative_signals") or heuristic["negative_signals"]
    if not isinstance(positive_signals, list):
        positive_signals = heuristic["positive_signals"]
    if not isinstance(negative_signals, list):
        negative_signals = heuristic["negative_signals"]

    label = parsed.get("label") or ("icp" if score >= 0.5 else "not-icp")
    if label not in ("icp", "not-icp"):
        label = "icp" if score >= 0.5 else "not-icp"

    recommended_action = parsed.get("recommended_action") or ("keep" if score >= 0.5 else "cancel")
    if recommended_action not in ("keep", "downgrade", "cancel"):
        recommended_action = "keep" if score >= 0.5 else "cancel"

    confidence_value = parsed.get("confidence", heuristic["confidence"])
    try:
        confidence = float(confidence_value)
    except Exception:
        confidence = float(heuristic["confidence"])

    return {
        "score": round(score, 3),
        "label": label,
        "confidence": round(confidence, 3),
        "reasoning": (parsed.get("reasoning") or heuristic["reasoning"]).strip(),
        "positive_signals": list(dict.fromkeys([str(x) for x in positive_signals if x]))[:10],
        "negative_signals": list(dict.fromkeys([str(x) for x in negative_signals if x]))[:10],
        "recommended_action": recommended_action,
        "model_used": getattr(result, "model", getattr(result, "model_used", "unknown")),
        "route": "review",
        "raw": raw[:2000],
    }


def score_lead(email: str = "", domain: str = "", homepage_text: str = "", homepage_url: str = "") -> dict[str, Any]:
    if not homepage_text and homepage_url:
        homepage_text, source_url = fetch_homepage_text(homepage_url)
    else:
        source_url = homepage_url or ""
        if not homepage_text and domain:
            homepage_text, source_url = fetch_homepage_text(domain)

    heuristic = heuristic_score(email, domain, homepage_text)
    llm = llm_score(email, domain, homepage_text, heuristic)

    # Soft blend: keep the model, but prevent obvious local-service false positives.
    final_score = llm["score"]
    if heuristic["negative_signals"] and heuristic["score"] < 0.25:
        final_score = min(final_score, 0.25)
    if heuristic["positive_signals"] and heuristic["score"] > 0.75:
        final_score = max(final_score, 0.75)

    label = "icp" if final_score >= 0.5 else "not-icp"
    return {
        "email": email,
        "domain": domain,
        "homepage_url": source_url,
        "homepage_excerpt": (homepage_text or "")[:1200],
        "score": round(final_score, 3),
        "label": label,
        "reasoning": llm["reasoning"],
        "confidence": llm["confidence"],
        "positive_signals": llm["positive_signals"],
        "negative_signals": llm["negative_signals"],
        "recommended_action": llm["recommended_action"] if label == "icp" else "cancel",
        "model_used": llm["model_used"],
        "route": llm["route"],
        "heuristic": heuristic,
    }


def _load_input_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    if path.suffix.lower() == ".jsonl":
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return rows
    data = json.loads(text)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("leads"), list):
            return [row for row in data["leads"] if isinstance(row, dict)]
        return [data]
    return []


def _as_row(record: dict[str, Any]) -> dict[str, Any]:
    email = record.get("email") or record.get("lead_email") or record.get("contact") or ""
    domain = record.get("domain") or record.get("website") or ""
    homepage_text = record.get("homepage_text") or record.get("homepage") or record.get("context") or ""
    homepage_url = record.get("homepage_url") or record.get("website") or record.get("url") or ""
    scored = score_lead(email=email, domain=domain, homepage_text=homepage_text, homepage_url=homepage_url)
    scored.update({
        "name": record.get("name") or record.get("lead_name") or record.get("company") or "",
        "company": record.get("company") or record.get("product") or "",
        "source": record.get("source") or record.get("source_kind") or "",
    })
    return scored


def main() -> int:
    _load_env_files()
    ap = argparse.ArgumentParser(description="Score founder ICP fit using opus-4-7 (route=review).")
    ap.add_argument("--email", default="")
    ap.add_argument("--domain", default="")
    ap.add_argument("--homepage-text", default="")
    ap.add_argument("--homepage-url", default="")
    ap.add_argument("--input-file", type=Path)
    ap.add_argument("--jsonl", action="store_true", help="Emit one JSON object per input row")
    ap.add_argument("--json-only", action="store_true", help="Suppress human-readable text")
    args = ap.parse_args()

    if args.input_file:
        rows = _load_input_file(args.input_file)
        results = [_as_row(row) for row in rows]
        if args.jsonl or args.json_only:
            for row in results:
                print(json.dumps(row, ensure_ascii=False))
        else:
            print(json.dumps({"count": len(results), "results": results}, indent=2, ensure_ascii=False))
        return 0

    scored = score_lead(
        email=args.email,
        domain=args.domain,
        homepage_text=args.homepage_text,
        homepage_url=args.homepage_url,
    )
    if args.json_only:
        print(json.dumps(scored, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(scored, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
