#!/usr/bin/env python3
"""TIER-3.5 sibling — Twilio SMS outbound channel.

Stdlib-only HTTPS wrapper around Twilio's REST API. Reads creds from
`~/.config/twilio/credentials.env` (or env if set). DNC list check before
every send. DRY-RUN default; live requires both `RICK_TWILIO_SMS_LIVE=1` AND
a real-looking AUTH_TOKEN (refuses to send on placeholder).

Public API:
  - load_creds() -> (sid, token, from_num, source)
  - send_sms(to, body, *, dry_run=None, suppress_check=True) -> dict
  - check_dnc(phone) -> bool

CLI:
  python3 -m runtime.integrations.twilio_sms send +15551234567 "hello"
  python3 -m runtime.integrations.twilio_sms send +15551234567 "test" --live
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DNC_FILE = DATA_ROOT / "control" / "dnc-list.txt"
LOG_FILE = DATA_ROOT / "operations" / "twilio-sms.jsonl"
CREDS_FILE = Path.home() / ".config" / "twilio" / "credentials.env"

E164 = re.compile(r"^\+\d{8,15}$")
TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(payload: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload["ts"] = _now_iso()
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except OSError:
        pass


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return out


def load_creds() -> tuple[str | None, str | None, str | None, str]:
    """Resolve (account_sid, auth_token, from_number, source).

    Env wins, then ~/.config/twilio/credentials.env. Source is one of
    'env' / 'file' / 'none'.
    """
    sid = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
    from_num = (os.getenv("TWILIO_PHONE_NUMBER") or "").strip()
    if sid and token and from_num:
        return sid, token, from_num, "env"
    if CREDS_FILE.is_file():
        env = _parse_env_file(CREDS_FILE)
        sid = sid or env.get("TWILIO_ACCOUNT_SID", "")
        token = token or env.get("TWILIO_AUTH_TOKEN", "")
        from_num = from_num or env.get("TWILIO_PHONE_NUMBER", "")
        if sid and token and from_num:
            return sid, token, from_num, "file"
    return None, None, None, "none"


def _looks_like_real_token(token: str) -> bool:
    """Real Twilio auth tokens are 32 hex chars. Reject obvious placeholders."""
    if not token or len(token) != 32:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in token)


def check_dnc(phone: str) -> bool:
    """Return True if phone is on DNC list."""
    if not DNC_FILE.is_file():
        return False
    needle = re.sub(r"[^\d+]", "", phone)
    if not needle:
        return False
    try:
        for line in DNC_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entry = re.sub(r"[^\d+]", "", line.split()[0])
            if entry and entry == needle:
                return True
    except OSError:
        pass
    return False


def _normalize_e164(phone: str) -> str | None:
    """Best-effort E.164 normalization. US default if 10 digits."""
    digits = re.sub(r"[^\d+]", "", phone or "")
    if not digits:
        return None
    if digits.startswith("+"):
        return digits if E164.match(digits) else None
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def send_sms(
    to: str,
    body: str,
    *,
    dry_run: bool | None = None,
    suppress_check: bool = True,
) -> dict:
    """Send one SMS via Twilio. Returns dict with status/result.

    dry_run: True = never POST, just log + return preview.
             False = actually send (requires RICK_TWILIO_SMS_LIVE=1).
             None = honor RICK_TWILIO_SMS_LIVE env (default DRY).
    """
    if dry_run is None:
        dry_run = os.getenv("RICK_TWILIO_SMS_LIVE", "0").strip().lower() not in ("1", "true", "yes")

    to_e164 = _normalize_e164(to)
    if not to_e164:
        result = {"status": "error", "error": "invalid-recipient", "to": to}
        _log({"phase": "validate", **result})
        return result

    body = (body or "").strip()
    if not body:
        return {"status": "error", "error": "empty-body"}
    if len(body) > 1500:
        body = body[:1497] + "..."

    if suppress_check and check_dnc(to_e164):
        result = {"status": "suppressed", "reason": "dnc-list", "to": to_e164}
        _log({"phase": "suppress", **result})
        return result

    sid, token, from_num, source = load_creds()
    if not sid or not token or not from_num:
        result = {"status": "error", "error": "no-credentials", "creds_source": source}
        _log({"phase": "creds", **result})
        return result

    preview = {"to": to_e164, "from": from_num, "body_len": len(body), "creds_source": source}

    if dry_run:
        result = {"status": "dry-run", **preview, "body_preview": body[:120]}
        _log({"phase": "dry-run", **result})
        return result

    if not _looks_like_real_token(token):
        result = {"status": "error", "error": "auth-token-placeholder", **preview}
        _log({"phase": "creds", **result})
        return result

    url = TWILIO_API.format(sid=urllib.parse.quote(sid, safe=""))
    data = urllib.parse.urlencode({"To": to_e164, "From": from_num, "Body": body}).encode("utf-8")
    auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "rick-twilio-sms/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        result = {
            "status": "sent",
            "sid": payload.get("sid"),
            "twilio_status": payload.get("status"),
            "to": to_e164,
            "from": from_num,
        }
        _log({"phase": "send", **result})
        return result
    except urllib.error.HTTPError as e:
        body_err = ""
        try:
            body_err = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        result = {"status": "error", "error": f"http-{e.code}", "detail": body_err[:200], "to": to_e164}
        _log({"phase": "send", **result})
        return result
    except Exception as exc:  # noqa: BLE001
        result = {"status": "error", "error": str(exc)[:200], "to": to_e164}
        _log({"phase": "send", **result})
        return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("send", help="Send one SMS")
    s.add_argument("to")
    s.add_argument("body")
    s.add_argument("--live", action="store_true", help="Actually send (otherwise dry-run)")
    s.add_argument("--no-dnc", action="store_true", help="Skip DNC check")

    sub.add_parser("creds", help="Show resolved creds source (no values)")
    sub.add_parser("dnc-test", help="Print DNC list summary").add_argument(
        "phone", nargs="?", default=""
    )

    args = ap.parse_args()

    if args.cmd == "creds":
        sid, token, from_num, source = load_creds()
        print(json.dumps({
            "source": source,
            "has_sid": bool(sid),
            "has_token": bool(token),
            "token_looks_real": _looks_like_real_token(token or ""),
            "has_from": bool(from_num),
            "from_number": from_num if from_num else None,
        }, indent=2))
        return 0

    if args.cmd == "dnc-test":
        on_list = check_dnc(args.phone) if args.phone else None
        print(json.dumps({
            "dnc_file": str(DNC_FILE),
            "exists": DNC_FILE.is_file(),
            "phone": args.phone or None,
            "on_dnc": on_list,
        }, indent=2))
        return 0

    if args.cmd == "send":
        result = send_sms(
            args.to, args.body,
            dry_run=not args.live,
            suppress_check=not args.no_dnc,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in ("sent", "dry-run") else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
