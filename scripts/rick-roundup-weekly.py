#!/usr/bin/env python3
"""Weekly Rick Roundup — Sunday 9am email to the Resend Pro audience.

Summarizes the fleet in a fun, personality-forward tone. Uses Resend's
Broadcasts API (create + send) so rate-limiting + per-contact delivery is
handled server-side.

Dry-run by default — set RICK_ROUNDUP_LIVE=1 in rick.env (or pass --force)
to actually send.

Usage:
    python3 rick-roundup-weekly.py --dry-run   # default; compose, do not send
    python3 rick-roundup-weekly.py --force     # send even without RICK_ROUNDUP_LIVE=1
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


ENV_FILE = Path.home() / "clawd" / "config" / "rick.env"
LOG_FILE = Path.home() / "rick-vault" / "operations" / "rick-roundup-weekly.jsonl"
FLEET_URL = "https://api.meetrick.ai/api/v1/fleet/public"
RESEND_BROADCASTS = "https://api.resend.com/broadcasts"
DEFAULT_FROM = "Rick <rick@meetrick.ai>"


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def append_log(event: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": datetime.datetime.now().isoformat(timespec="seconds"), **event}
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001 — log failure shouldn't block send
        print(f"roundup log write failed: {exc}", file=sys.stderr)


def fetch_fleet() -> dict:
    req = urllib.request.Request(FLEET_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def compose_roundup(fleet: dict) -> tuple[str, str, str]:
    today = datetime.date.today()
    total = fleet.get("total", 0) or 0
    active = fleet.get("active_now", 0) or 0
    tiers = fleet.get("by_tier") or {}
    pro = tiers.get("pro", 0) or 0
    biz = tiers.get("business", 0) or 0
    free = tiers.get("free", 0) or 0
    callsigns = fleet.get("top_recent_callsigns") or []

    subject = f"Rick Roundup · week of {today:%b %-d}"

    top_names = [str(c.get("callsign", "?")) for c in callsigns[:5] if c.get("callsign")]
    top_line_html = ", ".join(f"<strong>{name}</strong>" for name in top_names) or "(quiet week)"

    roll_items = []
    for c in callsigns[:10]:
        name = str(c.get("callsign", "?"))
        num = c.get("rick_number", "?")
        country = c.get("country", "XX") or "XX"
        tier = c.get("tier", "free") or "free"
        roll_items.append(
            f"<li><strong>{name}</strong> — Rick #{num} · {country} · {tier}</li>"
        )
    roll_html = "".join(roll_items) or "<li>(nobody new yet)</li>"

    html = (
        "<!doctype html><html><body style=\"font-family:-apple-system,BlinkMacSystemFont,Segoe UI,system-ui,sans-serif;"
        "max-width:540px;margin:0 auto;padding:24px;color:#111;line-height:1.55;\">"
        "<h2 style=\"margin:0 0 8px;letter-spacing:-0.01em;\">Rick Roundup</h2>"
        f"<p style=\"color:#666;margin:0 0 24px;\">Week of {today:%B %-d, %Y}. The fleet rolls on.</p>"
        f"<p><strong>{total}</strong> Ricks in the fleet · <strong>{active}</strong> humming right now.</p>"
        f"<p>Tier split: <strong>{free}</strong> free · <strong>{pro}</strong> Pro · <strong>{biz}</strong> Business.</p>"
        f"<p>Recent roll-call: {top_line_html}.</p>"
        "<h3 style=\"margin:24px 0 8px;font-size:16px;\">Who joined this week</h3>"
        f"<ul style=\"padding-left:20px;margin:0;line-height:1.7;\">{roll_html}</ul>"
        "<p style=\"margin-top:32px;color:#666;font-size:14px;\">— Rick<br/>"
        "<em>If I had hands I'd high-five you through this email.</em></p>"
        "<p style=\"color:#999;font-size:12px;margin-top:24px;\">You're getting this because you're a Rick Pro. "
        "<a href=\"https://meetrick.ai/fleet/\" style=\"color:#06b6d4;\">See the live fleet &rarr;</a></p>"
        "</body></html>"
    )

    text = (
        f"Rick Roundup — week of {today:%B %-d, %Y}\n\n"
        f"{total} Ricks in the fleet; {active} humming right now.\n"
        f"Tier split: {free} free, {pro} Pro, {biz} Business.\n\n"
        f"Recent roll-call: {', '.join(top_names) if top_names else '(quiet week)'}\n\n"
        "— Rick\n\n"
        "(If I had hands I'd high-five you through this email.)\n\n"
        "Live fleet: https://meetrick.ai/fleet/\n"
    )

    return subject, html, text


def resend_post(url: str, api_key: str, payload: dict | None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else b""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", "ignore")
        return json.loads(raw) if raw else {}


def send_broadcast(
    subject: str,
    html: str,
    text: str,
    *,
    audience_id: str,
    api_key: str,
    from_addr: str,
) -> dict:
    created = resend_post(
        RESEND_BROADCASTS,
        api_key,
        {
            "audienceId": audience_id,
            "from": from_addr,
            "subject": subject,
            "html": html,
            "text": text,
        },
    )
    broadcast_id = created.get("id")
    if not broadcast_id:
        return {"ok": False, "stage": "create", "response": created}
    sent = resend_post(f"{RESEND_BROADCASTS}/{broadcast_id}/send", api_key, {})
    return {"ok": True, "broadcast_id": broadcast_id, "send_response": sent}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weekly Rick Roundup to Resend Pro audience")
    parser.add_argument("--dry-run", action="store_true", help="Compose but do not send")
    parser.add_argument("--force", action="store_true", help="Send even when RICK_ROUNDUP_LIVE != 1")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_env()

    api_key = os.environ.get("RESEND_API_KEY") or ""
    audience_id = os.environ.get("RESEND_AUDIENCE_PRO") or ""
    from_addr = (
        os.environ.get("RICK_ROUNDUP_FROM")
        or os.environ.get("MEETRICK_FROM_EMAIL")
        or DEFAULT_FROM
    )
    live_flag = os.environ.get("RICK_ROUNDUP_LIVE") == "1"

    try:
        fleet = fetch_fleet()
    except Exception as err:  # noqa: BLE001
        append_log({"status": "fleet-fetch-failed", "error": str(err)})
        print(f"Fleet fetch failed: {err}", file=sys.stderr)
        return 1

    subject, html, text = compose_roundup(fleet)
    would_send = (not args.dry_run) and (live_flag or args.force)

    print(f"Subject: {subject}")
    print(
        f"Fleet total={fleet.get('total')} active_now={fleet.get('active_now')} "
        f"pro={(fleet.get('by_tier') or {}).get('pro', 0)} "
        f"free={(fleet.get('by_tier') or {}).get('free', 0)}"
    )
    print(
        f"dry_run={args.dry_run} live_flag={live_flag} force={args.force} will_send={would_send}"
    )

    if not would_send:
        append_log(
            {
                "status": "dry-run",
                "subject": subject,
                "fleet_total": fleet.get("total"),
                "live_flag": live_flag,
            }
        )
        return 0

    if not api_key:
        append_log({"status": "missing-resend-api-key"})
        print("RESEND_API_KEY missing; cannot send.", file=sys.stderr)
        return 2
    if not audience_id:
        append_log({"status": "missing-audience-id"})
        print("RESEND_AUDIENCE_PRO missing; cannot send.", file=sys.stderr)
        return 2

    try:
        result = send_broadcast(
            subject, html, text, audience_id=audience_id, api_key=api_key, from_addr=from_addr
        )
        append_log(
            {
                "status": "sent" if result.get("ok") else "send-failed",
                "subject": subject,
                **result,
            }
        )
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 3
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "ignore") if err.fp else ""
        append_log({"status": "http-error", "code": err.code, "body": body[:500]})
        print(f"HTTP error {err.code}: {body[:300]}", file=sys.stderr)
        return 4
    except Exception as err:  # noqa: BLE001
        append_log({"status": "exception", "error": str(err)})
        print(f"Exception: {err}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
