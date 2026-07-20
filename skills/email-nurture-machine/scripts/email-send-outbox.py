#!/usr/bin/env python3
"""Fix for broken outbox: reads outbox directory and sends emails via Resend API.

Scheduled consumer for deferred (send_after) outbox items: runs every 900s
from the ai.rick.email-sequence LaunchAgent chain, gated on
RICK_EMAIL_SEND_LIVE (dry-run otherwise). Wired 2026-07-14 after a paying
customer's access email (send_after 07:00) sat stranded until 07:44 because
nothing consumed deferred items.
SINGLE scheduled drain since 2026-07-17: walk_json_outbox (the redundant
second consumer in email-sequence-send.py, 07-14 double-send class) is
retired; concurrent daemon sends are excluded by the atomic .sending claim.
Usage: python3 email-send-outbox.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OUTBOX_DIR = DATA_ROOT / "mailbox" / "outbox"
SENT_DIR = DATA_ROOT / "mailbox" / "sent"
SENDS_LOG = DATA_ROOT / "operations" / "email-sends.jsonl"  # bounce-rate-guardian denominator
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"
VIOLATIONS_FILE = DATA_ROOT / "operations" / "suppression-violations.jsonl"
# Gate reasons that can never clear for this recipient (is_send_allowed
# prefixes): park the item instead of retrying every 900s forever.
# mina@example.test (2026-07-19) retried through the 15-min .test-TLD
# validator gap because placeholder blocks were treated as transient.
PERMANENT_BLOCK_PREFIXES = ("suppressed", "placeholder_domain", "role_account", "invalid_recipient")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
FROM_EMAIL = os.getenv("RICK_EMAIL_FROM", "rick@meetrick.ai")
MAX_PER_BATCH = 20


def _workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "runtime").is_dir():
            return parent
    return current.parents[3]


def email_channel_block_reason(transactional: bool = False) -> str | None:
    try:
        root = str(_workspace_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        from runtime.db import connect
        from runtime.kill_switches import ChannelPaused, assert_channel_active

        conn = connect()
        try:
            assert_channel_active(conn, "email", transactional=transactional)
            return None
        except ChannelPaused as exc:
            return exc.reason
        finally:
            conn.close()
    except Exception as exc:
        return f"gate_unavailable: {type(exc).__name__}: {exc}"


def transactional_email_types() -> frozenset[str]:
    """Item `type` values exempt from quiet hours (delivery/dunning).

    Canonical set lives in runtime.kill_switches.TRANSACTIONAL_EMAIL_TYPES.
    Fail-safe: if the import breaks, return the empty set — nothing is
    exempt and everything keeps the quiet-hours deferral (never the reverse).
    """
    try:
        root = str(_workspace_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        from runtime.kill_switches import TRANSACTIONAL_EMAIL_TYPES

        return TRANSACTIONAL_EMAIL_TYPES
    except Exception as exc:
        print(f"transactional_email_types unavailable ({exc}); quiet hours apply to all", file=sys.stderr)
        return frozenset()


def load_suppressions() -> set[str]:
    if not SUPPRESSION_FILE.exists():
        return set()
    try:
        lines = SUPPRESSION_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    suppressed: set[str] = set()
    for raw in lines:
        email = raw.split("#", 1)[0].strip().lower()
        if email:
            suppressed.add(email)
    return suppressed


def send_email(to: str, subject: str, body: str, cold: bool = False) -> dict:
    """Send a single email via Resend API."""
    # Unified fail-closed per-recipient gate (2026-07-13): master kill +
    # RICK_EMAIL_SEND_LIVE + merged suppression/DNC. Default cold=False
    # (outbox nurture goes to leads already in conversation) but honor an
    # explicit cold flag on the outbox item — first-touch cold drafts
    # (e.g. founder-sourcer) must get the 7-day cold frequency cap.
    # Mirrors phase1.handle_outbox_send; strictly strengthens the gate.
    try:
        import sys as _sys
        wsroot = str(Path(__file__).resolve().parents[3])
        if wsroot not in _sys.path:
            _sys.path.insert(0, wsroot)
        from runtime.kill_switches import is_send_allowed
        allowed, gate_reason = is_send_allowed(to, cold=cold)
    except Exception as exc:
        allowed, gate_reason = False, f"gate_unavailable:{type(exc).__name__}"
    if not allowed:
        print(f"SEND_BLOCKED reason={gate_reason} to={to}")
        return {"blocked": gate_reason}
    payload = json.dumps({
        "from": FROM_EMAIL,
        "to": [to],
        "subject": subject,
        "text": body,
    }).encode()

    # 2026-07-12 senders UA-fix: default Python-urllib UA gets 403'd by
    # Resend/CDN — send the same UA the other Resend senders use.
    req = Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json", "User-Agent": "meetrick-rick/1.0"},
        method="POST",
    )
    response = urlopen(req, timeout=15)
    return json.loads(response.read())


def extract_subject(body_md: str) -> str:
    """Extract subject from markdown body."""
    for line in body_md.splitlines():
        if line.startswith("**Subject:**"):
            return line.replace("**Subject:**", "").strip()
    return "Message from Rick"


def strip_subject_line(body_md: str) -> str:
    """Drop the '**Subject:** ...' carrier line AFTER extract_subject ran —
    it is routing metadata (the outbox item's only subject carrier), not
    copy the recipient should see (2026-07-17 polish)."""
    lines = body_md.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("**Subject:**"):
            del lines[i]
            return "\n".join(lines).lstrip("\n")
    return body_md


def process_outbox(dry_run: bool = False) -> dict:
    """Process all pending emails in the outbox."""
    if not OUTBOX_DIR.exists():
        return {"status": "empty", "sent": 0, "errors": 0}
    transactional_only = False
    if not dry_run:
        block_reason = email_channel_block_reason()
        if block_reason:
            # Quiet hours + the daily/warmup VOLUME caps defer marketing
            # only. Transactional mail (TRANSACTIONAL_EMAIL_TYPES: paid
            # access delivery, dunning) waives those clauses and must go out
            # now — re-check the gate with the waiver; if anything ABSOLUTE
            # blocks too (master kill, channel pause, per-minute pacing),
            # abort the whole run.
            residual = email_channel_block_reason(transactional=True)
            if residual:
                return {"status": "channel_paused", "reason": residual, "sent": 0, "errors": 0}
            transactional_only = True

    if not dry_run:
        SENT_DIR.mkdir(parents=True, exist_ok=True)
        # Orphaned-claim sweep (2026-07-17): a crash mid-send strands a
        # *.json.sending claim (see atomic claim below). After 60min — the
        # recent-send-cap window, which blocks a true duplicate on retry —
        # rename it back to *.json so the item is not silently lost.
        for stale in OUTBOX_DIR.glob("*.json.sending"):
            try:
                if datetime.now().timestamp() - stale.stat().st_mtime > 3600:
                    print(f"reclaiming orphaned claim {stale.name}", file=sys.stderr)
                    stale.rename(stale.with_name(stale.name[: -len(".sending")]))
            except OSError as exc:
                print(f"orphan-claim sweep failed for {stale.name}: {exc}", file=sys.stderr)
    now = datetime.now().isoformat(timespec="seconds")
    sent = 0
    errors = 0
    skipped = 0
    suppressions = load_suppressions()
    conn = None  # lazy runtime-DB connection for record_send bookkeeping

    for f in sorted(OUTBOX_DIR.iterdir()):
        if not f.suffix == ".json":
            continue
        if sent >= MAX_PER_BATCH:
            break

        try:
            msg = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        if msg.get("status") != "pending":
            continue

        # Check scheduled send time
        send_after = msg.get("send_after", "")
        if send_after and send_after > now:
            skipped += 1
            continue

        # Channel blocked for marketing (quiet hours / volume caps):
        # transactional (delivery/dunning) proceeds through the remaining
        # gates below; everything else waits for the release/reset.
        if transactional_only and str(msg.get("type") or "") not in transactional_email_types():
            skipped += 1
            continue

        to_email = msg.get("to", "")
        if not to_email:
            continue

        # Atomic claim (2026-07-17): same-dir rename BEFORE any further
        # action — POSIX rename has exactly one winner, so a concurrent
        # consumer (daemon phase1.handle_outbox_send) can never send the
        # same file; the loser hits OSError and skips. The .sending suffix
        # is invisible to every *.json scan. Dry-run stays fully read-only.
        claim = f
        if not dry_run:
            claim = f.with_name(f.name + ".sending")
            try:
                f.rename(claim)
                os.utime(claim)  # claim timestamp for the orphan sweep
            except OSError:
                continue  # another consumer claimed it first
            # Re-read post-claim: the pre-claim copy can be stale (owner
            # cancel or rewrite between the read above and the claim).
            try:
                msg = json.loads(claim.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                claim.rename(f)
                continue
            if msg.get("status") != "pending":
                claim.rename(f)
                continue
            to_email = msg.get("to", "")  # refresh from the post-claim read
            if not to_email:
                claim.rename(f)
                continue

        if to_email.strip().lower() in suppressions:
            if dry_run:
                # Dry-run must be fully read-only: report, don't rewrite/move.
                print(f"[DRY RUN] Would suppress {to_email}")
                skipped += 1
                continue
            msg["status"] = "suppressed"
            msg["suppressed_at"] = now
            claim.write_text(json.dumps(msg, indent=2), encoding="utf-8")
            claim.rename(SENT_DIR / f.name)
            skipped += 1
            print(f"Suppressed {to_email}")
            continue

        body_md = msg.get("body_markdown", msg.get("pitch_markdown", ""))
        subject = extract_subject(body_md)
        body_md = strip_subject_line(body_md)

        if dry_run:
            print(f"[DRY RUN] Would send to {to_email}: {subject}")
            sent += 1
            continue

        if not RESEND_API_KEY:
            print("RESEND_API_KEY not set — skipping send", file=sys.stderr)
            claim.rename(f)  # release the claim before bailing out
            return {"status": "no_api_key", "sent": 0, "errors": 0}

        try:
            result = send_email(to_email, subject, body_md, cold=bool(msg.get("cold", False)))
            blocked_reason = result.get("blocked")
            if blocked_reason:
                # Mirror phase1.handle_outbox_send: recipient-level blocks
                # (suppressed / placeholder domain / role account / invalid)
                # are permanent — park the message so it never retries, and
                # append the attempt to suppression-violations.jsonl (same
                # ledger resend-suppression-sync + formatters/email use).
                # Non-permanent blocks (master kill / live flag / frequency
                # cap) leave the file pending for a later run.
                reason = str(blocked_reason)
                if reason.startswith(PERMANENT_BLOCK_PREFIXES):
                    msg["status"] = "blocked"
                    msg["error"] = f"SEND_BLOCKED reason={reason}"[:200]
                    claim.write_text(json.dumps(msg, indent=2), encoding="utf-8")
                    errors += 1
                    try:
                        VIOLATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
                        with VIOLATIONS_FILE.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(
                                {"ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "violation": "outbox_drain_blocked",
                                 "to": to_email,
                                 "suppression_reason": reason,
                                 "outbox_file": f.name,
                                 "subject": subject}) + "\n")
                    except OSError as exc:
                        print(f"suppression-violations.jsonl append failed (item parked): {exc}", file=sys.stderr)
                else:
                    skipped += 1
                claim.rename(f)  # release the claim; blocked parks, skipped retries
                continue
            msg["status"] = "sent"
            msg["sent_at"] = now
            msg["resend_id"] = result.get("id", "")
            claim.write_text(json.dumps(msg, indent=2), encoding="utf-8")
            claim.rename(SENT_DIR / f.name)
            sent += 1
            print(f"Sent to {to_email}: {subject}")
            # Bookkeeping mirrors phase1.handle_outbox_send — shielded, must
            # never fail a send that already went out, but log the miss.
            # record_send bumps channel counters; the touch ledger flips the
            # queued outbound_jobs row to sent (or inserts one) so every
            # send is counted — ported from the retired walk_json_outbox.
            try:
                root = str(_workspace_root())
                if root not in sys.path:
                    sys.path.insert(0, root)
                from runtime.db import connect as _runtime_connect
                from runtime.kill_switches import record_send
                from runtime.touch_log import log_touch, mark_touch_sent

                if conn is None:
                    conn = _runtime_connect()
                record_send(conn, "email")
                if mark_touch_sent(conn, f.name) == 0:
                    log_touch(
                        conn, to=to_email, channel="email",
                        template_id=msg.get("type", "outbox"),
                        subject=subject,
                        variant=msg.get("variant", ""), skill=msg.get("skill", ""),
                        source=msg.get("source_channel", "outbox"),
                        status="sent", outbox_file=f.name,
                        workflow_id=msg.get("workflow_id", ""),
                    )
            except Exception as exc:
                print(f"send bookkeeping failed (send already out): {exc}", file=sys.stderr)
            # Ops send ledger — bounce-rate-guardian counts its denominator
            # from this file; row shape matches campaign-engine.py plus the
            # source attribution field carried over from walk_json_outbox.
            try:
                SENDS_LOG.parent.mkdir(parents=True, exist_ok=True)
                with SENDS_LOG.open("a") as handle:
                    handle.write(json.dumps(
                        {"message_id": msg.get("resend_id", ""),
                         "status": "sent",
                         "to": to_email,
                         "source": f"json-outbox-{msg.get('type', 'outbox')}",
                         "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")},
                        sort_keys=True) + "\n")
            except OSError as exc:
                print(f"email-sends.jsonl append failed (send already out): {exc}", file=sys.stderr)
        except Exception as exc:
            msg["status"] = "error"
            msg["error"] = str(exc)[:200]
            claim.write_text(json.dumps(msg, indent=2), encoding="utf-8")
            claim.rename(f)  # release the claim; status=error never retries
            errors += 1
            print(f"Error sending to {to_email}: {exc}", file=sys.stderr)

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    return {"status": "processed", "sent": sent, "errors": errors, "skipped": skipped}


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    result = process_outbox(dry_run=dry_run)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
