#!/usr/bin/env python3
"""resend-suppression-sync.py — Push local suppression list to Resend's API layer.

Reads ~/rick-vault/mailbox/suppression.txt, marks each address as
unsubscribed=true in ALL Resend audiences so any future broadcast send from
any code path is blocked at the API layer.

IDEMPOTENT: safe to re-run; Resend upserts contacts, no duplicates.
STATE FILE: only re-pushes addresses added since last sync run.

Usage:
    python3 scripts/resend-suppression-sync.py           # sync suppression list
    python3 scripts/resend-suppression-sync.py --all     # force-push all, ignore state
    python3 scripts/resend-suppression-sync.py --dry-run # show what would be pushed
    python3 scripts/resend-suppression-sync.py --probe   # check violation log only
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── paths ──────────────────────────────────────────────────────────────────────
# Use ~/rick-vault as the canonical data root regardless of RICK_DATA_ROOT env
# (which may point to a test install path when rick.env is sourced by LaunchAgents).
# The user-specified canonical suppression list lives at ~/rick-vault.
_VAULT = Path.home() / "rick-vault"
DATA_ROOT = _VAULT
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"
STATE_FILE = DATA_ROOT / "control" / "resend-suppression-sync-state.json"
VIOLATIONS_FILE = DATA_ROOT / "operations" / "suppression-violations.jsonl"
SENDS_FILE = DATA_ROOT / "operations" / "email-sends.jsonl"

# Source rick.env ONLY for the API key — do not let it override DATA_ROOT
_env_file = Path.home() / "clawd" / "config" / "rick.env"
if _env_file.exists() and not os.environ.get("RESEND_API_KEY"):
    for line in _env_file.read_text().splitlines():
        if "RESEND_API_KEY" in line and "=" in line:
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if val and val.startswith("re_"):
                os.environ["RESEND_API_KEY"] = val
                break

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
RESEND_BASE = "https://api.resend.com"
HEADERS = {
    "Authorization": f"Bearer {RESEND_API_KEY}",
    "Content-Type": "application/json",
    "User-Agent": "rick-suppression-sync/1.0",
}

# ── Resend audience IDs ─────────────────────────────────────────────────────
# Push to ALL audiences so broadcasts from any audience are blocked.
AUDIENCE_IDS = [
    "fc739eb9-0e59-4aec-a6d0-5bf208b2b3dd",  # General
    "ea127c5e-cf20-4ee7-afc2-1f1ece8b44b0",  # Rick Operators
    "8c9fdb02-4aba-44d8-a720-7ded07f4b30b",  # Rick Pro
]

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
# File extensions that can appear as fake TLDs in bad suppression entries
_FAKE_TLDS = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "mp4", "pdf", "csv",
    "json", "md", "html", "css", "js", "ts", "txt", "xml", "zip",
}


def _is_real_email(addr: str) -> bool:
    if not EMAIL_RE.match(addr):
        return False
    tld = addr.rsplit(".", 1)[-1].lower()
    return tld not in _FAKE_TLDS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ── suppression.txt parser ──────────────────────────────────────────────────
def load_suppressed() -> dict[str, str]:
    """Return {email: reason} for all suppressed addresses."""
    if not SUPPRESSION_FILE.exists():
        return {}
    result: dict[str, str] = {}
    for raw in SUPPRESSION_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("#", 1)
        email = parts[0].strip().lower()
        reason = parts[1].strip() if len(parts) > 1 else "suppressed"
        if _is_real_email(email):
            result[email] = reason
    return result


# ── state ─────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"synced": {}, "last_run": None}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Resend API calls ───────────────────────────────────────────────────────────
def upsert_contact(audience_id: str, email: str, *, dry_run: bool = False) -> tuple[bool, str]:
    """Mark email as unsubscribed in one Resend audience. Returns (ok, detail)."""
    if dry_run:
        return True, "dry-run"
    payload = json.dumps({"email": email, "unsubscribed": True}).encode()
    req = Request(
        f"{RESEND_BASE}/audiences/{audience_id}/contacts",
        data=payload,
        headers=HEADERS,
        method="POST",
    )
    try:
        resp = urlopen(req, timeout=15)
        body = json.loads(resp.read())
        return True, body.get("id", "ok")
    except HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return False, f"HTTP {exc.code}: {body[:120]}"
    except URLError as exc:
        return False, f"URLError: {exc.reason}"


# ── violation probe ───────────────────────────────────────────────────────────
def run_probe(suppressed: dict[str, str]) -> int:
    """Check today's email-sends.jsonl for sends to suppressed addresses.
    Logs violations to suppression-violations.jsonl. Returns violation count."""
    if not SENDS_FILE.exists():
        print("  no email-sends.jsonl found — skipping probe")
        return 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT")
    existing_keys: set[tuple[str, str, str]] = set()
    if VIOLATIONS_FILE.exists():
        try:
            for raw in VIOLATIONS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if row.get("violation") != "send_to_suppressed":
                    continue
                send_entry = row.get("send_entry") if isinstance(row.get("send_entry"), dict) else {}
                existing_keys.add((
                    str(row.get("to") or "").strip().lower(),
                    str(row.get("send_ts") or ""),
                    str(send_entry.get("message_id") or send_entry.get("id") or ""),
                ))
        except OSError:
            pass
    violations: list[dict] = []

    with SENDS_FILE.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts = entry.get("ts", "")
            if not ts.startswith(today[:10]):  # today's sends only
                continue
            to = (entry.get("to") or "").strip().lower()
            if to in suppressed:
                key = (to, str(ts), str(entry.get("message_id") or entry.get("id") or ""))
                if key in existing_keys:
                    continue
                violations.append({
                    "ts": now_iso(),
                    "violation": "send_to_suppressed",
                    "to": to,
                    "suppression_reason": suppressed[to],
                    "send_ts": ts,
                    "send_entry": entry,
                })

    if violations:
        VIOLATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with VIOLATIONS_FILE.open("a", encoding="utf-8") as fh:
            for v in violations:
                fh.write(json.dumps(v) + "\n")
        print(f"  ⚠️  {len(violations)} SUPPRESSION VIOLATION(S) logged to {VIOLATIONS_FILE}")
        # Try to alert via notify_operator_deduped
        try:
            import sqlite3
            db_path = DATA_ROOT / "runtime" / "rick-runtime.db"
            if not db_path.exists():
                db_path = Path.home() / ".openclaw" / "workspace" / "runtime" / "rick-runtime.db"
            if db_path.exists():
                sys.path.insert(0, str(Path.home() / ".openclaw" / "workspace"))
                from runtime.engine import notify_operator_deduped
                conn = sqlite3.connect(str(db_path))
                addrs = ", ".join(v["to"] for v in violations[:3])
                msg = (
                    f"🚨 SUPPRESSION VIOLATION: {len(violations)} email(s) sent to suppressed "
                    f"addresses today: {addrs}. Details: {VIOLATIONS_FILE}"
                )
                notify_operator_deduped(
                    conn, msg, kind="suppression_violation", dedup_window_hours=6
                )
                conn.close()
                print("  alert sent via notify_operator_deduped")
        except Exception as exc:
            print(f"  alert dispatch failed (non-fatal): {exc}")
    else:
        print("  ✓ no suppression violations in today's sends")

    return len(violations)


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    dry_run = "--dry-run" in sys.argv
    force_all = "--all" in sys.argv
    probe_only = "--probe" in sys.argv

    if not RESEND_API_KEY and not dry_run:
        print("ERROR: RESEND_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    suppressed = load_suppressed()
    total = len(suppressed)
    print(f"resend-suppression-sync — {now_iso()}")
    print(f"  suppression.txt: {total} addresses")

    if probe_only:
        print("  [probe-only mode]")
        run_probe(suppressed)
        return

    state = load_state()
    already_synced: dict[str, str] = state.get("synced", {})

    # Determine which addresses need pushing
    if force_all:
        to_push = suppressed
        print(f"  --all: force-pushing all {total} addresses")
    else:
        to_push = {e: r for e, r in suppressed.items() if e not in already_synced}
        print(f"  new since last sync: {len(to_push)}  (already synced: {len(already_synced)})")

    if dry_run:
        print(f"  [dry-run] would push {len(to_push)} addresses to {len(AUDIENCE_IDS)} audiences")
        for email in list(to_push.keys())[:5]:
            print(f"    → {email}")
        if len(to_push) > 5:
            print(f"    … and {len(to_push)-5} more")
        print("  [dry-run] probe check:")
        run_probe(suppressed)
        return

    pushed = 0
    errors = 0
    for email in to_push:
        for aud_id in AUDIENCE_IDS:
            ok, detail = upsert_contact(aud_id, email)
            if not ok:
                print(f"  ✗ {email} → audience {aud_id[-8:]}: {detail}", file=sys.stderr)
                errors += 1
            else:
                pushed += 1
            time.sleep(0.05)  # gentle pacing — 3 audiences × N emails

        already_synced[email] = now_iso()

    # Save state after successful push
    state["synced"] = already_synced
    state["last_run"] = now_iso()
    state["total_suppressed"] = total
    save_state(state)

    print(f"  ✓ pushed {len(to_push)} address(es) × {len(AUDIENCE_IDS)} audiences "
          f"= {pushed} upserts, {errors} errors")

    # Always run violation probe
    print("  running violation probe…")
    run_probe(suppressed)


if __name__ == "__main__":
    main()
