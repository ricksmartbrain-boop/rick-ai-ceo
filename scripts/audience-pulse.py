#!/usr/bin/env python3
"""funnel-pulse.py — daily top-of-funnel + email health snapshot.

Single source of truth so we never fly blind again. Pulls:
  - Resend audience contact counts (active vs unsubscribed)
  - Recent send outcomes (delivered/bounced/suppressed) from Resend
  - Computes recent bounce rate (reputation guard)
  - Writes a dated pulse line to ~/rick-vault/operations/funnel-pulse.jsonl

No model touches. Deterministic. Read-only against external APIs.

Usage:
  python3 scripts/funnel-pulse.py            # print + append
  python3 scripts/funnel-pulse.py --quiet    # append only
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RESEND_KEY = os.environ.get("RESEND_API_KEY", "")
OPS = Path(os.path.expanduser("~/rick-vault/operations"))
PULSE = OPS / "funnel-pulse.jsonl"

AUDIENCES = {
    "General": "fc739eb9-0e59-4aec-a6d0-5bf208b2b3dd",
    "RickPro": "8c9fdb02-4aba-44d8-a720-7ded07f4b30b",
    "Operators": "ea127c5e-cf20-4ee7-afc2-1f1ece8b44b0",
}


def _get(url: str, timeout: float = 15.0):
    # Resend rejects header-less urllib requests with 403; UA + Accept required.
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {RESEND_KEY}",
        "User-Agent": "rick-funnel-pulse/1.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def audience_counts():
    out = {}
    for name, aid in AUDIENCES.items():
        try:
            d = _get(f"https://api.resend.com/audiences/{aid}/contacts")
            data = d.get("data", [])
            unsub = sum(1 for c in data if c.get("unsubscribed"))
            out[name] = {"total": len(data), "active": len(data) - unsub, "unsubscribed": unsub}
        except Exception as exc:
            out[name] = {"error": type(exc).__name__}
    return out


def send_health():
    try:
        d = _get("https://api.resend.com/emails?limit=100")
        data = d.get("data", [])
        ev = {}
        for e in data:
            k = e.get("last_event", "?")
            ev[k] = ev.get(k, 0) + 1
        total = len(data)
        bounced = ev.get("bounced", 0)
        suppressed = ev.get("suppressed", 0)
        rate = round(100 * (bounced + suppressed) / total, 1) if total else 0.0
        return {"recent_sends": total, "events": ev, "bounce_suppress_pct": rate}
    except Exception as exc:
        return {"error": type(exc).__name__}


def main():
    quiet = "--quiet" in sys.argv
    if not RESEND_KEY:
        print("ERROR: RESEND_API_KEY not set", file=sys.stderr)
        return 1

    pulse = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "audiences": audience_counts(),
        "send_health": send_health(),
    }

    OPS.mkdir(parents=True, exist_ok=True)
    with PULSE.open("a") as f:
        f.write(json.dumps(pulse) + "\n")

    if not quiet:
        g = pulse["audiences"].get("General", {})
        sh = pulse["send_health"]
        print("=== FUNNEL PULSE", pulse["ts"][:16], "===")
        print(f"General list: {g.get('active','?')} active / {g.get('total','?')} total")
        for n in ("RickPro", "Operators"):
            a = pulse["audiences"].get(n, {})
            print(f"{n}: {a.get('active','?')} active / {a.get('total','?')} total")
        print(f"Recent send health: {sh.get('recent_sends','?')} sends, "
              f"bounce+suppress {sh.get('bounce_suppress_pct','?')}%")
        print(f"  events: {sh.get('events', {})}")
        if isinstance(sh.get("bounce_suppress_pct"), (int, float)) and sh["bounce_suppress_pct"] > 5:
            print("  ⚠️  bounce+suppress > 5% — reputation risk, investigate before bulk send")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
