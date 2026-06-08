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
# Fabricated / guessed local-part guard
# ---------------------------------------------------------------------------

# Local parts that are almost always fabricated guesses produced by
# scrape-and-blast jobs (e.g. "email@medspaoftampa.com"). These pass MX
# checks because the domain is real, but the mailbox does not exist and the
# message hard-bounces, poisoning domain reputation.
_FABRICATED_LOCALS: frozenset[str] = frozenset({
    "email", "youremail", "name", "yourname", "firstname", "lastname",
    "example", "test", "none", "null", "na", "n/a", "unknown",
})

# US city tokens commonly used as fabricated local parts by the lead scraper
# (e.g. "tampa@medspaoftampa.com"). Kept small and append-only.
_CITY_LOCALS: frozenset[str] = frozenset({
    "tampa", "orlando", "sacramento", "detroit", "memphis", "louisville",
    "baltimore", "pittsburgh", "albuquerque", "cincinnati", "columbus",
    "cleveland", "omaha", "richmond", "denver", "saltlakecity", "slc",
})


def is_fabricated_local(email: str) -> bool:
    """Return True if the local part looks fabricated/guessed (not a real mailbox)."""
    try:
        local, _ = _parse_parts(email)
    except ValueError:
        return False
    clean_local = re.split(r'[+.]', local)[0].lower()
    return clean_local in _FABRICATED_LOCALS or clean_local in _CITY_LOCALS


# ---------------------------------------------------------------------------
# SMTP RCPT probe — verifies the mailbox actually accepts mail
# ---------------------------------------------------------------------------

def smtp_mailbox_exists(email: str, timeout: float = 8.0,
                        helo_host: str = "meetrick.ai",
                        mail_from: str = "rick@meetrick.ai") -> Tuple[bool, str]:
    """Probe the recipient's mail server with RCPT TO to confirm the mailbox.

    Returns (accepts, reason). Conservative: on ANY ambiguity (greylisting,
    timeout, connection refused, 4xx) returns (True, ...) so we never block a
    legitimate address on a flaky probe. Only a hard 5xx RCPT rejection (550
    "no such user" family) returns (False, ...).
    """
    import smtplib
    try:
        _, domain = _parse_parts(email)
    except ValueError:
        return False, "invalid_format"

    # Resolve best MX host
    mx_host = domain
    try:
        import dns.resolver  # type: ignore
        answers = dns.resolver.resolve(domain, "MX", lifetime=timeout)
        mx_host = str(sorted(answers, key=lambda r: r.preference)[0].exchange).rstrip(".")
    except Exception:
        mx_host = domain  # fall back to domain A record

    # Hard guard: if port 25 is blocked outbound (common on many hosts), a
    # connect can hang well past the smtplib timeout. Pre-check with a short
    # raw socket connect and bail fast (fail-open) if unreachable.
    try:
        import socket as _sock
        probe = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        probe.settimeout(min(timeout, 5.0))
        try:
            probe.connect((mx_host, 25))
        finally:
            probe.close()
    except Exception as exc:
        return True, f"port25_unreachable:{type(exc).__name__}"

    try:
        server = smtplib.SMTP(timeout=timeout)
        server.connect(mx_host, 25)
        server.helo(helo_host)
        server.mail(mail_from)
        code, _msg = server.rcpt(email)
        try:
            server.quit()
        except Exception:
            pass
        if code in (250, 251):
            return True, "rcpt_accepted"
        if 500 <= code < 600:
            return False, f"rcpt_rejected:{code}"
        # 4xx / greylist / unknown → don't block
        return True, f"ambiguous:{code}"
    except Exception as exc:
        # Connection refused, timeout, blocked port 25, etc. → don't block
        return True, f"probe_unavailable:{type(exc).__name__}"


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

    if is_fabricated_local(email):
        return False, f"fabricated_local:{local}"

    if not has_mx_record(email):
        return False, f"no_mx_record:{domain}"

    return True, "ok"


def deep_validate_for_outbound(email: str) -> Tuple[bool, str]:
    """validate_for_outbound + live SMTP RCPT mailbox probe.

    Use this on COLD outbound (scraped/guessed addresses) where mailbox
    existence is uncertain. The SMTP probe adds latency, so reserve it for
    cold blasts, not transactional/subscriber sends.
    """
    ok, reason = validate_for_outbound(email)
    if not ok:
        return ok, reason
    accepts, sreason = smtp_mailbox_exists(email)
    if not accepts:
        return False, sreason
    return True, f"ok:{sreason}"


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
