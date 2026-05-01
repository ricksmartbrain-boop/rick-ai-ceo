"""runtime/email_validator.py — MX-record + validity checks for outbound email.

Public API
----------
has_mx_record(email)        → bool   DNS MX query, 5 s timeout, 1 retry
is_disposable_domain(email) → bool   check against built-in blocklist
is_role_account(email)      → bool   info@/contact@/etc.
validate_for_outbound(email)→ (ok: bool, reason: str)   combines all checks

Design constraints
------------------
- Standard-library only unless dnspython is installed (weaker fallback via socket).
- DNS lookup is the heavy part: 5 s timeout, retry once on transient failure.
- No model touches — pure deterministic logic.
- Thread-safe (all state is local / module-level frozensets).
"""

from __future__ import annotations

import socket
import re
from typing import Tuple

# ---------------------------------------------------------------------------
# Built-in disposable / throwaway domain list (common culprits)
# ---------------------------------------------------------------------------
_DISPOSABLE_DOMAINS: frozenset[str] = frozenset({
    # Mailinator family
    "mailinator.com", "trashmail.com", "trashmail.net", "trashmail.me",
    "trashmail.io", "trashmail.at", "spam4.me", "spamgourmet.com",
    # Guerrilla Mail family
    "guerrillamail.com", "guerrillamail.net", "guerrillamail.org",
    "guerrillamail.biz", "guerrillamail.de", "guerrillamail.info",
    "sharklasers.com", "guerrillamailblock.com",
    # 10 Minute Mail
    "10minutemail.com", "10minutemail.net", "10minutemail.org",
    "10minutemail.de", "10minemail.com",
    # Yopmail
    "yopmail.com", "yopmail.fr", "cool.fr.nf", "jetable.fr.nf",
    "nospam.ze.tc", "nomail.xl.cx",
    # Temp-mail
    "temp-mail.org", "temp-mail.ru", "tempmail.com", "tempmail.net",
    "tempmail.org", "tempr.email",
    # Misc popular throwaway services
    "fakeinbox.com", "throwam.com", "throwam.net", "dispostable.com",
    "mailnull.com", "maildrop.cc", "mailnesia.com", "mailnull.com",
    "spamoff.de", "bugmenot.com", "binkmail.com", "bobmail.info",
    "chammy.info", "devnullmail.com", "fastacura.com", "fudgerub.com",
    "garliclife.com", "getonemail.com", "lol.ovpn.to", "mt2009.com",
    "mx0.wwwnew.eu", "nomail.xl.cx", "objectmail.com", "ownmail.net",
    "petemails.com", "pookmail.com", "rppkn.com", "spamavert.com",
    "spamcorptastic.com", "spamfree24.org", "spamgob.com", "spamherelots.com",
    "spamhereplease.com", "spamthisplease.com", "stuffmail.de",
    "supergreatmail.com", "supermailer.jp", "suremail.info",
    "thisisnotmyrealemail.com", "throwam.com", "tnef.com", "tradermail.info",
    "trash-mail.at", "trash2009.com", "trashdevil.com", "trashemail.de",
    "upliftnow.com", "uroid.com", "vomoto.com", "webemail.me", "wilemail.com",
    "wspya.de", "ya.ru", "yam.com", "zehnminutenmail.de",
    # Inboxbear / sharedmail
    "inboxbear.com", "sharedmailbox.org", "mailmetrash.com",
    # Cвременные адреса
    "cuvox.de", "dayrep.com", "einrot.com", "fleckens.hu",
    "guam.net", "ixxo.com", "jetable.com", "jetable.net",
    "jetable.org", "netzidiot.de", "nicebush.com", "odaymail.com",
    "ownmail.net", "pepemail.com", "rhyta.com", "spamgob.com",
    "spamgourmet.net", "spamgourmet.org", "techemail.com",
    "teleworm.com", "teleworm.us", "trbvm.com", "uroid.com",
    # EmailOnDeck / Getnada / similar
    "emailondeck.com", "getnada.com", "luxusmail.org",
    "moakt.cc", "moakt.co", "moakt.com", "moakt.ws",
})

# ---------------------------------------------------------------------------
# Role-account prefixes that indicate non-personal addresses
# ---------------------------------------------------------------------------
_ROLE_PREFIXES: tuple[str, ...] = (
    "info", "contact", "hello", "admin", "office", "support",
    "team", "mail", "sales", "help", "service", "billing",
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "enquiries", "enquiry", "feedback", "general", "hr",
    "jobs", "legal", "marketing", "media", "news", "pr",
    "privacy", "procurement", "purchase", "recruiting",
    "recruitment", "security", "spam", "unsubscribe",
    "webmaster", "postmaster", "hostmaster",
)

# ---------------------------------------------------------------------------
# Basic format check
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _parse_parts(email: str) -> Tuple[str, str]:
    """Return (local, domain) lowercased.  Raises ValueError on bad format."""
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValueError(f"Invalid email format: {email!r}")
    local, domain = email.rsplit("@", 1)
    return local, domain


# ---------------------------------------------------------------------------
# MX lookup — dnspython preferred, socket fallback
# ---------------------------------------------------------------------------

def _mx_via_dnspython(domain: str, timeout: float) -> bool:
    """Returns True if domain has at least one MX record."""
    import dns.resolver  # type: ignore
    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    try:
        answers = resolver.resolve(domain, "MX")
        return len(answers) > 0
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer,
            dns.resolver.NoNameservers, dns.exception.Timeout):
        return False


def _mx_via_socket(domain: str, timeout: float) -> bool:
    """Weaker fallback: checks A/AAAA record existence as a proxy for domain validity.

    This does NOT verify an MX record specifically — it only confirms the domain
    resolves.  Use dnspython for production-grade MX checks.
    """
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        socket.getaddrinfo(domain, None)
        return True
    except (socket.gaierror, socket.timeout, OSError):
        return False
    finally:
        socket.setdefaulttimeout(old)


def has_mx_record(email: str, timeout: float = 5.0) -> bool:
    """Return True if the email's domain has a valid MX (or A) record.

    Tries dnspython for a proper MX query.  Falls back to socket.getaddrinfo
    when dnspython is unavailable (weaker but non-blocking).

    Retries once on transient failure before returning False.
    """
    try:
        _, domain = _parse_parts(email)
    except ValueError:
        return False

    for _attempt in range(2):  # retry once
        try:
            try:
                import dns.resolver  # type: ignore  # noqa: F401
                result = _mx_via_dnspython(domain, timeout)
            except ImportError:
                result = _mx_via_socket(domain, timeout)
            return result
        except Exception:
            # Transient / unexpected error — retry
            continue
    return False


# ---------------------------------------------------------------------------
# Disposable domain check
# ---------------------------------------------------------------------------

def is_disposable_domain(email: str) -> bool:
    """Return True if the email's domain is in the built-in disposable list."""
    try:
        _, domain = _parse_parts(email)
    except ValueError:
        return False
    return domain in _DISPOSABLE_DOMAINS


# ---------------------------------------------------------------------------
# Role account check
# ---------------------------------------------------------------------------

def is_role_account(email: str) -> bool:
    """Return True if the local part is a generic role address (info@, admin@, …)."""
    try:
        local, _ = _parse_parts(email)
    except ValueError:
        return False
    # Exact match or prefix+delimiter match (e.g. "support+tickets" still role)
    clean_local = re.split(r'[+.]', local)[0]
    return clean_local in _ROLE_PREFIXES


# ---------------------------------------------------------------------------
# Combined outbound gate
# ---------------------------------------------------------------------------

def validate_for_outbound(email: str) -> Tuple[bool, str]:
    """Run all checks and return (ok, reason).

    Fast checks run first (no I/O).  DNS check runs last (slow path).
    Returns on first failure — callers should log the reason.

    ok=True  → safe to use for outbound
    ok=False → skip / add to suppression
    """
    if not email or "@" not in email:
        return False, "empty_or_no_at"

    try:
        local, domain = _parse_parts(email)
    except ValueError:
        return False, "invalid_format"

    if is_disposable_domain(email):
        return False, f"disposable_domain:{domain}"

    if is_role_account(email):
        return False, f"role_account:{local}"

    if not has_mx_record(email):
        return False, f"no_mx_record:{domain}"

    return True, "ok"


# ---------------------------------------------------------------------------
# CLI smoke-test helper
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    test_cases = [
        "vladislav@belkins.io",
        "fake-noexist@nowhere-1234.com",
        "info@somerestaurant.com",
        "trash@mailinator.com",
        "rick@meetrick.ai",
    ]

    if len(sys.argv) > 1:
        test_cases = sys.argv[1:]

    print("email_validator smoke-test\n" + "=" * 48)
    for addr in test_cases:
        ok, reason = validate_for_outbound(addr)
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"{status}  {addr:<40}  reason={reason}")
