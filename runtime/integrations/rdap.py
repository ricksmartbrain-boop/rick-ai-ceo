"""RDAP (Registration Data Access Protocol) helpers.

Stdlib-only. RDAP replaces classic WHOIS — returns structured JSON describing
domain ownership, registrar, registration date, contacts, and name servers.

Two query paths:
  1. Generic IANA bootstrap registry (rdap.iana.org) — returns the per-TLD
     RDAP server URL.
  2. Per-TLD direct (cached).

Public RDAP servers are rate-limited but free. No auth required for most TLDs.
For .ai / .dev / .io / .co / .so / .app the bootstrap registry resolves
correctly to the appropriate registry server.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Most-recent successful per-TLD lookup so we skip the bootstrap call.
# Bootstrap responses change rarely (months); a process-lifetime cache is fine.
_TLD_CACHE: dict[str, str] = {}

USER_AGENT = "Rick-WHOIS-Firehose/1.0 (+https://meetrick.ai)"
RDAP_BOOTSTRAP = "https://data.iana.org/rdap/dns.json"
RDAP_GENERIC = "https://rdap.org/domain/{}"


def _http_get(url: str, timeout: int = 12) -> dict[str, Any] | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
            return json.loads(data)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return None


def _load_tld_cache() -> None:
    """Populate _TLD_CACHE from the IANA bootstrap registry once per process."""
    if _TLD_CACHE:
        return
    bootstrap = _http_get(RDAP_BOOTSTRAP, timeout=15)
    if not bootstrap or "services" not in bootstrap:
        return
    for service in bootstrap.get("services", []):
        if not isinstance(service, list) or len(service) < 2:
            continue
        tlds, urls = service[0], service[1]
        if not (isinstance(tlds, list) and isinstance(urls, list) and urls):
            continue
        # Prefer https endpoint
        endpoint = next((u for u in urls if u.startswith("https://")), urls[0])
        if not endpoint.endswith("/"):
            endpoint += "/"
        for tld in tlds:
            _TLD_CACHE[tld.lower().lstrip(".")] = endpoint


def lookup_domain(domain: str, *, retries: int = 1, sleep_between_s: float = 0.4) -> dict[str, Any] | None:
    """Return parsed RDAP record for `domain`, or None on lookup failure.

    Tries TLD-specific registry first (via IANA bootstrap), falls back to the
    public rdap.org redirect service. None is returned for unregistered domains
    OR for any error — callers should treat None as "no usable record".
    """
    domain = (domain or "").strip().lower().lstrip(".")
    if not domain or "." not in domain:
        return None

    _load_tld_cache()
    tld = domain.rsplit(".", 1)[-1]
    candidates: list[str] = []
    if tld in _TLD_CACHE:
        candidates.append(_TLD_CACHE[tld] + "domain/" + urllib.parse.quote(domain))
    candidates.append(RDAP_GENERIC.format(urllib.parse.quote(domain)))

    last_payload: dict[str, Any] | None = None
    for attempt in range(retries + 1):
        for url in candidates:
            payload = _http_get(url)
            if payload and isinstance(payload, dict) and payload.get("ldhName"):
                return _flatten(payload)
            last_payload = payload
            time.sleep(sleep_between_s)
        time.sleep(sleep_between_s)
    return last_payload if last_payload and isinstance(last_payload, dict) else None


def _flatten(rdap: dict[str, Any]) -> dict[str, Any]:
    """Reduce an RDAP record to the few fields a lead-scoring loop needs."""
    out: dict[str, Any] = {
        "domain": (rdap.get("ldhName") or "").lower(),
        "handle": rdap.get("handle"),
        "status": rdap.get("status") or [],
        "registrar": "",
        "registrant_org": "",
        "registrant_country": "",
        "abuse_email": "",
        "name_servers": [],
        "events": {},
        "secure_dns": bool(rdap.get("secureDNS", {}).get("delegationSigned")) if isinstance(rdap.get("secureDNS"), dict) else False,
    }

    for ev in rdap.get("events", []) or []:
        action = (ev.get("eventAction") or "").lower().replace(" ", "_")
        date = ev.get("eventDate") or ""
        if action and date:
            out["events"][action] = date

    for ns in rdap.get("nameservers", []) or []:
        name = (ns.get("ldhName") or "").lower()
        if name:
            out["name_servers"].append(name)

    for ent in rdap.get("entities", []) or []:
        roles = [r.lower() for r in (ent.get("roles") or [])]
        # vCard contact extraction
        vcard = ent.get("vcardArray") or []
        if not (isinstance(vcard, list) and len(vcard) >= 2 and isinstance(vcard[1], list)):
            vcard = []
        else:
            vcard = vcard[1]
        org = ""
        country = ""
        email = ""
        for entry in vcard:
            if not isinstance(entry, list) or not entry:
                continue
            kind = entry[0]
            value = entry[-1] if len(entry) >= 4 else ""
            if kind == "fn" and not org:
                org = str(value or "")
            elif kind == "org":
                org = str(value or "")
            elif kind == "adr" and isinstance(value, list) and len(value) >= 7:
                country = str(value[6] or "")
            elif kind == "email":
                email = str(value or "")
        if "registrar" in roles and org:
            out["registrar"] = org
        if "registrant" in roles:
            if org:
                out["registrant_org"] = org
            if country:
                out["registrant_country"] = country
        if "abuse" in roles and email:
            out["abuse_email"] = email

    return out


def score_record(record: dict[str, Any]) -> float:
    """Cheap heuristic 0-10 score: prioritize fresh, identified, premium-TLD domains."""
    if not record:
        return 0.0
    score = 0.0
    domain = (record.get("domain") or "").lower()
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""

    if tld in {"ai", "dev", "io", "app"}:
        score += 1.5
    elif tld in {"co", "so", "xyz"}:
        score += 0.8

    org = (record.get("registrant_org") or "").strip()
    if org and not any(k in org.lower() for k in ("redacted", "privacy", "withheld", "data protected")):
        score += 1.5
        if any(k in org.lower() for k in ("inc", "llc", "ltd", "corp", "labs", "studio", "ventures")):
            score += 0.5

    # Freshness: recent registration is a stronger founder signal
    reg = record.get("events", {}).get("registration") or record.get("events", {}).get("created")
    if isinstance(reg, str) and len(reg) >= 10:
        try:
            from datetime import datetime, timezone
            reg_dt = datetime.fromisoformat(reg.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - reg_dt).days
            if age_days <= 7:
                score += 3.0
            elif age_days <= 30:
                score += 2.0
            elif age_days <= 90:
                score += 1.0
        except (ValueError, TypeError):
            pass

    if record.get("abuse_email"):
        score += 0.5
    if len(record.get("name_servers") or []) >= 2:
        score += 0.3

    return round(min(score, 10.0), 2)
