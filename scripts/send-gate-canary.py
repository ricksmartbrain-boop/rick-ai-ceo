#!/usr/bin/env python3
"""send-gate-canary.py — verify every send path is wired to the unified gate.

Sends NOTHING. Five layers of checks:
  1. Functional: kill_switches.is_send_allowed blocks a known-suppressed
     address (colin@hfs1991.com, on control/dnc-list.txt) and supports
     domain-level entries ('@folderly.com').
  2. Wiring: every send-path source file calls the unified gate (or, for the
     cold-call dispatcher, the merged-DNC + RICK_VOICE_LIVE guards).
  3. Cold-call functional: lead_suppressed() and normalize_phone() behave.
  4. Tree-grep: no ungated api.resend.com/emails poster in the source tree
     outside the operator/transactional allowlist.
  5. Cron-store grep: no ENABLED OpenClaw cron job whose payload embeds a
     send-API endpoint (the 2026-06-29 raw-curl-to-Resend bypass class —
     payloads live in openclaw.sqlite, invisible to the tree-grep).

Run: python3 scripts/send-gate-canary.py        (exit 0 = all PASS)
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
VAULT = Path.home() / "rick-vault"

CANARY_ADDRESS = "colin@hfs1991.com"  # opted out 2026-06-15; on dnc-list.txt

# Send-path source files → markers that must ALL appear in the source.
SEND_PATHS = {
    "nurture-dispatch": (WORKSPACE_ROOT / "nurture-dispatch.py", ["is_send_allowed", "SEND_BLOCKED"]),
    "drip-sender": (WORKSPACE_ROOT / "scripts" / "drip-sender.py", ["is_send_allowed", "SEND_BLOCKED"]),
    "campaign-engine": (WORKSPACE_ROOT / "scripts" / "campaign-engine.py", ["is_send_allowed", "SEND_BLOCKED"]),
    "newsletter-engine": (WORKSPACE_ROOT / "scripts" / "newsletter-engine-run.py", ["is_send_allowed", "SEND_BLOCKED"]),
    "email-sequence-send": (
        WORKSPACE_ROOT / "skills" / "email-automation" / "scripts" / "email-sequence-send.py",
        ["is_send_allowed", "SEND_BLOCKED"],
    ),
    "follow-up-automation": (WORKSPACE_ROOT / "scripts" / "follow-up-automation.py", ["is_send_allowed", "SEND_BLOCKED"]),
    "followup-blast": (WORKSPACE_ROOT / "scripts" / "followup-blast.py", ["is_send_allowed", "SEND_BLOCKED"]),
    "phase1-outbox-send": (
        WORKSPACE_ROOT / "runtime" / "skill_handlers" / "phase1.py",
        ["is_send_allowed", "SEND_BLOCKED"],
    ),
    "gmail-personal-smtp": (
        WORKSPACE_ROOT / "runtime" / "formatters" / "gmail_personal.py",
        ["is_send_allowed", "SEND_BLOCKED"],
    ),
    "email_safety-shared (lead-machine/blast scripts)": (
        WORKSPACE_ROOT / "scripts" / "email_safety.py",
        ["is_send_allowed", "SEND_BLOCKED"],
    ),
    "cold-call-dispatch": (
        VAULT / "scripts" / "cold-call-dispatch.py",
        ["lead_suppressed", "RICK_VOICE_LIVE", "within_calling_hours"],
    ),
    "fix-sequence (hourly roast nurture)": (
        WORKSPACE_ROOT / "scripts" / "fix-sequence.py",
        ["is_send_allowed", "SEND_BLOCKED"],
    ),
}

# Files allowed to POST api.resend.com/emails WITHOUT the unified gate:
# operator alerts and double-opt-in transactional mail only — never
# marketing/outreach. Anything else ungated fails the tree-grep check.
TREE_GREP_ALLOWLIST = {
    "resend-bounce-poll.py",       # bounce-spike alert to operator
    "audience-pulse.py",           # audience report to operator
    "funnel-attribution.py",       # attribution report to operator
    "install-rick.sh",             # install welcome (opt-in transactional)
    "newsletter-subscribe.sh",     # subscribe confirmation (opt-in)
    "drip-trigger.sh",             # drip kickoff wrapper (drip itself gated)
    "resend-safe-send.sh",         # manual operator utility
    "rick-roundup-weekly.py",      # RICK_ROUNDUP_LIVE-gated broadcast
    "post-install-nudge.py",       # RICK_POST_INSTALL_NUDGE_LIVE-gated
    "critical-window-monitor.py",  # operator alert path
    "funnel-pulse.py",             # operator report
    "resend-suppression-sync.py",  # suppression API sync (no /emails send)
    "rick-guardian.py",            # scans FOR the pattern (cron-store check), sends nothing
}

# OpenClaw's live cron store — open strictly read-only (mode=ro); the gateway
# owns this WAL database. RICK_OPENCLAW_STATE_DB override exists for tests
# against a copy (same override pattern as rick-guardian.py).
OPENCLAW_STATE_DB = Path(os.getenv(
    "RICK_OPENCLAW_STATE_DB",
    str(Path.home() / ".openclaw" / "state" / "openclaw.sqlite")))

# Send-API endpoints that must never appear in a cron payload. Same poster
# pattern the tree-grep uses — but with NO gate-marker exemption: a cron
# payload cannot call the Python gate, so an embedded send endpoint is a raw
# (ungated, unsuppressed, unlogged) send path no matter what else it says.
CRON_SEND_API_PATTERNS = ("api.resend.com/emails",)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    # ── 1. Functional gate checks ────────────────────────────────────────
    root = str(WORKSPACE_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from runtime.kill_switches import is_send_allowed, is_suppressed_address

        allowed, reason = is_send_allowed(CANARY_ADDRESS, cold=True)
        results.append((
            f"gate blocks {CANARY_ADDRESS}",
            not allowed,
            f"allowed={allowed} reason={reason}",
        ))
        results.append((
            f"suppression list contains {CANARY_ADDRESS}",
            is_suppressed_address(CANARY_ADDRESS),
            "merged mailbox/suppression.txt + control/dnc-list.txt",
        ))
        results.append((
            "domain-level entry '@folderly.com' matches",
            is_suppressed_address("anyone@folderly.com", {"@folderly.com"}),
            "synthetic set",
        ))
        ok_addr, ok_reason = is_send_allowed("rick+qa-canary@meetrick.ai", cold=False)
        results.append((
            "gate returns a reason string for a clean address",
            isinstance(ok_reason, str) and len(ok_reason) > 0,
            f"allowed={ok_addr} reason={ok_reason}",
        ))
    except Exception as exc:
        results.append(("kill_switches gate import", False, f"{type(exc).__name__}: {exc}"))

    # ── 2. Wiring checks: every send path references the gate ───────────
    for label, (path, markers) in SEND_PATHS.items():
        if not path.exists():
            results.append((f"wired: {label}", False, f"missing file {path}"))
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            results.append((f"wired: {label}", False, f"unreadable: {exc}"))
            continue
        missing = [m for m in markers if m not in src]
        results.append((
            f"wired: {label}",
            not missing,
            f"missing markers: {missing}" if missing else str(path),
        ))

    # ── 3. Cold-call functional checks (no network, no dialing) ─────────
    try:
        ccd = _load_module("cold_call_dispatch_canary", VAULT / "scripts" / "cold-call-dispatch.py")
        dnc = ccd.load_dnc()
        results.append((
            "cold-call pre-dial blocks suppressed lead",
            bool(ccd.lead_suppressed({"email": CANARY_ADDRESS}, dnc)),
            f"merged DNC entries={len(dnc)}",
        ))
        bad = ccd.normalize_phone("1783613216")
        good = ccd.normalize_phone("(212) 655-0123")
        results.append((
            "normalize_phone rejects NANP-invalid scrape junk",
            bad == "" and good == "+12126550123",
            f"'1783613216'->{bad!r} '(212) 655-0123'->{good!r}",
        ))
    except Exception as exc:
        results.append(("cold-call functional checks", False, f"{type(exc).__name__}: {exc}"))

    # ── 4. Tree-grep: no ungated Resend senders outside attic/allowlist ──
    offenders: list[str] = []
    for base in (WORKSPACE_ROOT / "scripts", WORKSPACE_ROOT / "runtime",
                 WORKSPACE_ROOT / "skills", VAULT / "scripts"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".sh"):
                continue
            if "attic" in path.parts or path.name.endswith((".bak", ".bak-2026-07-13")) or ".bak" in path.name:
                continue
            if path.name in TREE_GREP_ALLOWLIST or path.name == Path(__file__).name:
                continue
            try:
                src = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "api.resend.com/emails" in src and "is_send_allowed" not in src and "email_safety" not in src:
                offenders.append(str(path))
    results.append((
        "tree-grep: no ungated api.resend.com/emails senders",
        not offenders,
        f"offenders: {offenders}" if offenders else "scripts/runtime/skills/vault clean",
    ))

    # ── 5. Cron-store grep: no ENABLED send-API cron payloads ───────────
    # 2026-06-29 bypass class: an Iris turn created deleteAfterRun OpenClaw
    # crons whose payloads curl'd api.resend.com/emails directly — skipping
    # is_send_allowed, suppression lists AND email-sends.jsonl. Two fired
    # unseen (2026-07-02/07-06). Payloads live in the cron store, not the
    # tree, so check 4 never saw them. ENABLED matches FAIL outright;
    # disabled matches WARN-but-pass (known offenders stay disabled and
    # owner-gated: Diane pair 0f85c506/1543d7a7 pending apr_556965a09442).
    # Missing/unreadable store FAILS loud — this check never passes blind.
    cron_check = "cron-store grep: no ENABLED send-API cron payloads"
    try:
        if not OPENCLAW_STATE_DB.exists():
            raise RuntimeError(f"cron store missing: {OPENCLAW_STATE_DB}")
        ccon = sqlite3.connect(f"file:{OPENCLAW_STATE_DB}?mode=ro", uri=True,
                               timeout=5)
        try:
            cron_rows = ccon.execute(
                "SELECT job_id, name, enabled, payload_message, job_json "
                "FROM cron_jobs"
            ).fetchall()
        finally:
            ccon.close()
        enabled_hits: list[str] = []
        disabled_hits: list[str] = []
        for job_id, job_name, enabled, message, job_json in cron_rows:
            blob = ((message or "") + " " + (job_json or "")).lower()
            if not any(p in blob for p in CRON_SEND_API_PATTERNS):
                continue
            if enabled:
                enabled_hits.append(f"{job_id} ({str(job_name)[:40]})")
            else:
                disabled_hits.append(f"{str(job_id)[:8]} ({str(job_name)[:40]})")
        if enabled_hits:
            cron_detail = f"ENABLED gate-bypass payloads: {enabled_hits}"
            if disabled_hits:
                cron_detail += f"; disabled matches: {disabled_hits}"
        elif disabled_hits:
            cron_detail = (
                f"{len(cron_rows)} jobs scanned, 0 enabled matches; WARN "
                f"known-disabled (keep disabled, owner-gated): {disabled_hits}")
        else:
            cron_detail = f"{len(cron_rows)} jobs scanned, payloads clean"
        results.append((cron_check, not enabled_hits, cron_detail))
    except Exception as exc:
        results.append((cron_check, False, f"{type(exc).__name__}: {exc}"))

    # ── report ───────────────────────────────────────────────────────────
    failed = 0
    for name, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"[{status}] {name} — {detail}")
    print(f"\n{len(results) - failed}/{len(results)} checks passed" + (" — ALL SEND PATHS GATED" if failed == 0 else " — FIX FAILURES BEFORE ANY SEND"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
