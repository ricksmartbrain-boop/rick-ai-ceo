#!/usr/bin/env python3
"""Extract structured contact data from email bodies + signatures.

Produces: {name, title, company, phone, emails, linkedin, urls, twitter}
Stdlib-only (no talon-signature dep). Conservative — returns partial fields
rather than wild guesses.
"""
from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://[\w./\-_%#?=&+~:;@]+", re.IGNORECASE)
LINKEDIN_RE = re.compile(r"linkedin\.com/(?:in|company)/[\w\-_.]+", re.IGNORECASE)
TWITTER_RE = re.compile(r"(?:twitter\.com|x\.com)/[\w\-_]+", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
# Common signature delimiters (ordered by specificity)
SIG_DELIMS = [
    re.compile(r"^--\s*$", re.MULTILINE),            # RFC-standard
    re.compile(r"^-+\s*$", re.MULTILINE),            # dashed
    re.compile(r"^_{3,}\s*$", re.MULTILINE),         # underscored
    re.compile(r"\n\s*(?:Best|Thanks|Cheers|Regards|Sincerely|Best regards|Kind regards)(?:,|!|\.|\s)", re.IGNORECASE),
    re.compile(r"\n\s*Sent from my (?:iPhone|Android|Mobile)", re.IGNORECASE),
]

# Reject lines that look like salutations / closings / quoted prior emails
NOISE_PATTERNS = (
    r"^On .+ wrote:",      # reply quote header
    r"^>",                 # quoted line
    r"^From: ",            # forward header
    r"^To: ",
    r"^Sent: ",
    r"^Subject: ",
    r"^http",              # URL-only line
    r"^\s*$",              # empty
    r"^-+$",
    r"^_+$",
)
NOISE_RE = re.compile("|".join(f"(?:{p})" for p in NOISE_PATTERNS), re.IGNORECASE | re.MULTILINE)


def _find_signature_block(body: str, max_lines: int = 10) -> str:
    """Return the likely signature block — the chunk of text near the bottom
    after a known delimiter. If no delimiter, return last `max_lines` non-quoted lines."""
    body = body.replace("\r\n", "\n").strip()
    # 1. Try explicit delimiters (first match wins from end of text)
    for delim in SIG_DELIMS:
        matches = list(delim.finditer(body))
        if matches:
            # Take text AFTER the last match
            start = matches[-1].end()
            tail = body[start:].strip()
            if 10 < len(tail) < 2000:
                return tail
    # 2. Fallback: last N non-noise lines
    lines = body.split("\n")
    kept = []
    for line in reversed(lines):
        if NOISE_RE.match(line):
            continue
        kept.append(line)
        if len(kept) >= max_lines:
            break
    return "\n".join(reversed(kept)).strip()


def _guess_name_title_company(sig: str) -> tuple[str, str, str]:
    """Heuristic: first line = Name, next line = Title, next = Company (or Title @ Company)."""
    lines = [L.strip() for L in sig.split("\n") if L.strip()]
    if not lines:
        return "", "", ""
    # Filter out obvious non-name lines
    candidates = []
    for line in lines[:8]:
        if EMAIL_RE.search(line) or PHONE_RE.search(line) or URL_RE.search(line):
            continue
        if len(line) > 100:
            continue
        if len(line.split()) > 8:
            continue
        candidates.append(line)
        if len(candidates) >= 4:
            break
    name = candidates[0] if candidates else ""
    # Validate "name": should have 2-4 words, Capitalized
    if name:
        words = name.split()
        if not (1 < len(words) <= 4 and all(w[:1].isupper() for w in words if w)):
            name = ""
    # Title + Company
    title = ""
    company = ""
    if len(candidates) >= 2:
        line2 = candidates[1]
        # Pattern: "Title at Company" OR "Title @ Company" OR "Title, Company"
        for sep in (" at ", " @ ", " | ", ", "):
            if sep.lower() in line2.lower():
                parts = re.split(f"(?i){re.escape(sep)}", line2, maxsplit=1)
                if len(parts) == 2:
                    title = parts[0].strip()
                    company = parts[1].strip()
                    break
        if not title:
            title = line2
        if not company and len(candidates) >= 3:
            company = candidates[2]
    return name, title, company


def extract_signature(body: str) -> dict[str, Any]:
    """Parse structured contact from email body (signature + inline).

    Returns dict with keys: name, title, company, phone, emails (list),
    linkedin, twitter, urls (list), raw_signature.
    """
    if not body:
        return {}
    sig_block = _find_signature_block(body)
    # Search inside the sig block first (highest signal), then full body as backup
    search_text = sig_block if sig_block else body

    emails = list({e.lower() for e in EMAIL_RE.findall(search_text)})
    urls_raw = URL_RE.findall(search_text)
    urls = list(dict.fromkeys(urls_raw))  # dedupe preserving order
    phones = list(dict.fromkeys(PHONE_RE.findall(search_text)))
    linkedin_match = LINKEDIN_RE.search(search_text)
    twitter_match = TWITTER_RE.search(search_text)

    name, title, company = _guess_name_title_company(sig_block)

    result: dict[str, Any] = {}
    if name:
        result["name"] = name
    if title:
        result["title"] = title[:100]
    if company:
        result["company"] = company[:100]
    if phones:
        result["phone"] = phones[0]
    if emails:
        result["emails"] = emails[:5]
    if linkedin_match:
        result["linkedin"] = linkedin_match.group(0)
    if twitter_match:
        result["twitter"] = twitter_match.group(0)
    if urls:
        # Filter out linkedin/twitter URLs that already matched
        other = [u for u in urls if "linkedin.com" not in u.lower() and "twitter.com" not in u.lower() and "x.com" not in u.lower()]
        if other:
            result["urls"] = other[:5]
    if sig_block:
        result["raw_signature"] = sig_block[:500]
    return result


if __name__ == "__main__":
    import sys
    import json as _json
    sample = sys.stdin.read() if not sys.stdin.isatty() else """
Hey Rick,

Thanks for the note. We're already using a similar tool but happy to chat.

Best,

Jamie Chen
Head of Growth at Acme Inc
+1 (415) 555-2390
jamie@acme.co
https://acme.co
https://linkedin.com/in/jamiechen
"""
    print(_json.dumps(extract_signature(sample), indent=2))
