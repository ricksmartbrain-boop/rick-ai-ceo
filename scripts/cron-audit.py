#!/usr/bin/env python3
"""
cron-audit.py — Rick LaunchAgent forensic auditor.

Usage:
    python3 scripts/cron-audit.py [--json] [--md OUTPUT.md]

Produces a markdown (or JSON) report covering:
  - All ai.rick.* + ai.meetrick.* plists
  - launchctl load status
  - Schedule (interval or calendar)
  - Script path + existence check
  - Kill-switch flags found in script body
  - External send potential (email/API/post)
  - Log freshness (last 24h)
  - Recent DB outbound job counts
  - Recommendations
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
ENV_FILE = Path.home() / "clawd" / "config" / "rick.env"
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
RUNTIME_DB = Path(os.getenv("RICK_RUNTIME_DB_FILE",
    str(Path.home() / "rick-vault" / "runtime" / "rick-runtime.db")))

# Patterns that indicate external send capability
SEND_PATTERNS = [
    r"resend", r"smtp", r"sendmail", r"send_email", r"requests\.post",
    r"outbound_dispatcher", r"fan_out", r"moltbook", r"linkedin.*post",
    r"instagram.*post", r"threads.*post", r"reddit.*post", r"AirDrop",
    r"airdrop", r"stripe.*charge", r"elevenlabs", r"twilio",
]

# Known kill-switch env flags (complete)
# Matches both positive gates (RICK_*_LIVE) and negative gates (RICK_*_DISABLED)
KILL_SWITCH_PATTERNS = [
    r"RICK_\w+_LIVE", r"RICK_\w+_DISABLED", r"DRY_RUN", r"--dry-run", r"--live",
    r"dry_run\s*=", r"RICK_OUTBOUND_ENABLED",
]

# ── Helpers ─────────────────────────────────────────────────────────────────

def load_env(env_file: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not env_file.exists():
        return env
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_loaded_agents() -> set[str]:
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=10
        ).stdout
        return {
            parts[2]
            for line in out.splitlines()
            if (parts := line.split()) and len(parts) >= 3
            and (parts[2].startswith("ai.rick.") or parts[2].startswith("ai.meetrick."))
        }
    except Exception:
        return set()


def get_loaded_agent_pids() -> dict[str, str]:
    """Return {label: pid_or_dash} for loaded agents."""
    result = {}
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=10
        ).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and (
                parts[2].startswith("ai.rick.") or parts[2].startswith("ai.meetrick.")
            ):
                result[parts[2]] = parts[0]  # pid or '-'
    except Exception:
        pass
    return result


def parse_plist(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)
    except Exception as e:
        return {"_error": str(e)}


def schedule_str(p: dict) -> str:
    si = p.get("StartInterval")
    sci = p.get("StartCalendarInterval")
    if si:
        mins = si // 60
        if mins < 60:
            return f"every {mins}m"
        return f"every {si // 3600}h"
    if sci:
        if isinstance(sci, list):
            return f"calendar×{len(sci)} (e.g. {sci[0]})"
        parts = []
        if "Weekday" in sci:
            days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]
            parts.append(days[sci["Weekday"]] if sci["Weekday"] < 7 else f"wd{sci['Weekday']}")
        if "Hour" in sci:
            parts.append(f"{sci['Hour']:02d}:{sci.get('Minute',0):02d}")
        return "daily " + " ".join(parts) if parts else str(sci)
    return "on-load/keepalive"


def stale_threshold_hours(p: dict) -> float:
    """Return max expected silence in hours before a loaded cron is flagged STALE."""
    si = p.get("StartInterval")
    sci = p.get("StartCalendarInterval")
    if si:
        # Interval jobs: flag if silent for >3× their interval, capped at 24h
        return min((si / 3600) * 3, 24.0)
    if sci:
        if isinstance(sci, list):
            first = sci[0] if sci else {}
            if isinstance(first, dict) and "Weekday" in first:
                return 9 * 24.0  # weekly (list form): allow up to 9 days silence
            return 36.0  # multi-time calendar: treat as daily
        if "Weekday" in sci:
            return 9 * 24.0  # weekly: allow up to 9 days silence
        return 36.0  # plain daily
    return 48.0  # on-load/keepalive


def get_prog(p: dict) -> str:
    prog = p.get("ProgramArguments") or p.get("Program") or []
    if isinstance(prog, list):
        return " ".join(prog)
    return str(prog)


def get_log(p: dict) -> tuple[str, str]:
    return (
        p.get("StandardOutPath", ""),
        p.get("StandardErrorPath", ""),
    )


def log_freshness(log_path: str) -> tuple[str, int, str]:
    """Returns (FRESH/STALE/MISSING, line_count, last_modified_str)."""
    if not log_path:
        return "NO_LOG", 0, ""
    p = Path(log_path)
    if not p.exists():
        return "MISSING", 0, ""
    mtime = p.stat().st_mtime
    age_s = datetime.now().timestamp() - mtime
    lines = sum(1 for _ in open(p, errors="replace"))
    last_mod = datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
    status = "FRESH" if age_s < 86400 else "STALE"
    return status, lines, last_mod


# Directories to search when resolving `-m module.name` invocations
_MODULE_ROOTS = [
    Path.home() / "clawd",
    Path.home() / ".openclaw" / "workspace",
    Path.home() / "rick-vault",
]
# Prefixes that indicate a non-script binary (Chrome, system tools, etc.)
_NON_SCRIPT_BINARY_PREFIXES = (
    "/Applications/",
    "/System/",
    "/usr/bin/env",
)


def _resolve_module_path(module_name: str) -> Path | None:
    """Try to find the .py file for a `-m module.name` invocation."""
    rel = Path(module_name.replace(".", "/") + ".py")
    for root in _MODULE_ROOTS:
        candidate = root / rel
        if candidate.exists():
            return candidate
    return None


def scan_script_body(prog_str: str) -> tuple[list[str], list[str], bool]:
    """
    Returns (kill_switches_found, send_patterns_found, script_exists).
    Scans the first resolved Python/shell script path in the prog string.

    script_exists=True when:
      - at least one .py/.sh path resolves on disk, OR
      - the command is a -m module invocation whose module file exists, OR
      - the command launches a known non-script binary (Chrome, /usr/bin/env, etc.)
    script_exists=False ONLY when paths are found but NONE of them exist on disk.
    (Empty path list = non-script binary → treated as exists to avoid false-positive BROKEN.)
    """
    # 1. Expand tilde paths before regex matching
    expanded = prog_str.replace("~/", str(Path.home()) + "/")

    # 2. Check for non-script binary invocations (Chrome, /System, etc.)
    for prefix in _NON_SCRIPT_BINARY_PREFIXES:
        if prefix in expanded:
            # Scan the full prog string for kill-switch patterns (no body to read)
            return [], [], True  # binary exists; can't scan body

    # 3. Find explicit .py / .sh paths
    paths = re.findall(r"(/[\w./\-]+\.(?:py|sh))", expanded)

    # 4. Check for -m module invocations
    module_match = re.search(r"-m\s+([\w.]+)", expanded)

    kill_found: list[str] = []
    send_found: list[str] = []
    found_any_file = False

    # Helper: scan a single file body for patterns
    def scan_body(body: str) -> None:
        for pattern in KILL_SWITCH_PATTERNS:
            for h in re.findall(pattern, body):
                if h not in kill_found:
                    kill_found.append(h)
        for pattern in SEND_PATTERNS:
            if re.search(pattern, body, re.IGNORECASE):
                label = pattern.replace(r"\.", ".").replace(r"\w+", "*").replace(r"\s*", "")
                if label not in send_found:
                    send_found.append(label)

    for script_path in paths:
        p = Path(script_path)
        if not p.exists():
            continue
        found_any_file = True
        try:
            scan_body(p.read_text(errors="replace"))
        except Exception:
            pass

    if module_match and not found_any_file:
        mod_path = _resolve_module_path(module_match.group(1))
        if mod_path:
            found_any_file = True
            try:
                scan_body(mod_path.read_text(errors="replace"))
            except Exception:
                pass

    # If we found explicit paths but NONE existed → truly broken
    # If we found NO paths AND no module match → unknown binary, treat as exists
    if paths and not found_any_file:
        return kill_found, send_found, False  # truly BROKEN
    return kill_found, send_found, True  # exists (found, module-based, or unknown binary)


def get_outbound_db_stats() -> dict[str, dict]:
    """Returns {channel: {done, queued, failed, last_24h_done}} from DB."""
    stats: dict[str, dict] = {}
    if not RUNTIME_DB.exists():
        return stats
    try:
        conn = sqlite3.connect(str(RUNTIME_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT channel, status, COUNT(*) as cnt FROM outbound_jobs GROUP BY channel, status"
        ).fetchall()
        for row in rows:
            ch = row["channel"]
            if ch not in stats:
                stats[ch] = {"done": 0, "queued": 0, "failed": 0, "last_24h": 0}
            st = row["status"]
            if st == "done":
                stats[ch]["done"] += row["cnt"]
            elif st in ("queued", "pending"):
                stats[ch]["queued"] += row["cnt"]
            elif st == "failed":
                stats[ch]["failed"] += row["cnt"]
        rows24 = conn.execute(
            "SELECT channel, COUNT(*) as cnt FROM outbound_jobs "
            "WHERE status='done' AND created_at > datetime('now','-1 day') "
            "GROUP BY channel"
        ).fetchall()
        for row in rows24:
            ch = row["channel"]
            if ch not in stats:
                stats[ch] = {"done": 0, "queued": 0, "failed": 0, "last_24h": 0}
            stats[ch]["last_24h"] = row["cnt"]
        conn.close()
    except Exception:
        pass
    return stats


# ── Risk classification ─────────────────────────────────────────────────────

def risk_level(record: dict) -> str:
    """Return DANGER / WATCH / SAFE / JUNK based on findings."""
    send = record.get("send_patterns", [])
    kills = record.get("kill_switches", [])
    status = record.get("log_status", "")
    loaded = record.get("loaded", False)
    script_exists = record.get("script_exists", True)
    # BUG FIX: was `any(gen for k in list or any(...))` — the `or` never evaluated
    has_global_kill = (
        any(k in kills for k in ["RICK_OUTBOUND_ENABLED", "DRY_RUN"])
        or any("RICK_" in k and "_LIVE" in k for k in kills)
        or any("RICK_" in k and "_DISABLED" in k for k in kills)  # negative kill-switches
    )

    if not loaded:
        return "UNLOADED"
    if not script_exists:
        return "BROKEN"
    if not send:
        return "SAFE"
    # Has send capability
    if has_global_kill:
        # Check if live flag is set in env
        return "WATCH"
    # No kill switch but has send
    return "DANGER"


# ── Main audit ─────────────────────────────────────────────────────────────

def audit() -> list[dict]:
    plists = sorted(
        list(LAUNCH_AGENTS_DIR.glob("ai.rick.*.plist")) +
        list(LAUNCH_AGENTS_DIR.glob("ai.meetrick.*.plist"))
    )
    loaded = get_loaded_agents()
    pids = get_loaded_agent_pids()
    env = load_env(ENV_FILE)
    db_stats = get_outbound_db_stats()

    records = []
    for plist_path in plists:
        p = parse_plist(plist_path)
        if "_error" in p:
            records.append({"label": plist_path.stem, "error": p["_error"]})
            continue

        label = p.get("Label", plist_path.stem)
        prog = get_prog(p)
        sched = schedule_str(p)
        stdout, stderr = get_log(p)
        log_status, log_lines, log_mtime = log_freshness(stdout)

        # Also check work logs redirected inside the -lc command (>> /path/to/file)
        # Many crons redirect output inside zsh -lc, so StandardOutPath may be stale
        # while the actual work log is fresh.
        if log_status in ("STALE", "MISSING", "NO_LOG"):
            expanded_prog = prog.replace("~/", str(Path.home()) + "/")
            work_logs = re.findall(r">>\s*([/~][\w./\-]+\.log)", expanded_prog)
            work_logs = [wl.replace("~/", str(Path.home()) + "/") for wl in work_logs]
            for wl in work_logs:
                wl_status, wl_lines, wl_mtime = log_freshness(wl)
                if wl_status == "FRESH" or (wl_mtime and wl_mtime > log_mtime):
                    # Work log is fresher — use it
                    log_status, log_lines, log_mtime = wl_status, wl_lines, wl_mtime
                    break

        kill_switches, send_patterns, script_exists = scan_script_body(prog)
        is_loaded = label in loaded
        pid = pids.get(label, "-")
        disabled = p.get("Disabled", False)
        # Schedule-aware stale threshold
        stale_hours = stale_threshold_hours(p)
        # Re-evaluate FRESH/STALE using schedule-aware threshold
        if log_mtime and log_mtime != "":
            try:
                # Recompute age in hours from mtime string (MM-DD HH:MM)
                log_dt = datetime.strptime(f"{datetime.now().year}-{log_mtime}", "%Y-%m-%d %H:%M")
                age_hours = (datetime.now() - log_dt).total_seconds() / 3600
                # Handle year rollover for Jan logs checked in Dec
                if age_hours < -1:
                    log_dt = log_dt.replace(year=datetime.now().year - 1)
                    age_hours = (datetime.now() - log_dt).total_seconds() / 3600
                effective_status = "FRESH" if age_hours <= stale_hours else "STALE"
            except Exception:
                effective_status = log_status
        else:
            effective_status = log_status

        # Determine live_flags from env
        live_flags_set = [k for k in kill_switches if "RICK_" in k and "_LIVE" in k and env.get(k) == "1"]

        rec = {
            "label": label,
            "loaded": is_loaded,
            "pid": pid,
            "disabled": disabled,
            "schedule": sched,
            "prog": prog[:120],
            "script_exists": script_exists,
            "log_status": effective_status,
            "log_status_raw": log_status,  # original 24h threshold status
            "log_lines": log_lines,
            "log_mtime": log_mtime,
            "stale_threshold_h": stale_hours,
            "kill_switches": kill_switches,
            "live_flags_set": live_flags_set,
            "send_patterns": send_patterns,
        }
        rec["risk"] = risk_level(rec)
        records.append(rec)

    return records, db_stats


# ── Report rendering ────────────────────────────────────────────────────────

RISK_EMOJI = {
    "DANGER":   "🔴",
    "WATCH":    "🟡",
    "SAFE":     "🟢",
    "JUNK":     "⚫",
    "UNLOADED": "⬜",
    "BROKEN":   "💀",
}


def render_markdown(records: list[dict], db_stats: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# Rick LaunchAgent Cron Audit — {now}", ""]

    total = len(records)
    loaded_count = sum(1 for r in records if r.get("loaded"))
    unloaded_count = sum(1 for r in records if not r.get("loaded"))
    danger = [r for r in records if r.get("risk") == "DANGER"]
    watch = [r for r in records if r.get("risk") == "WATCH"]
    broken = [r for r in records if r.get("risk") == "BROKEN"]
    # Schedule-aware stale: use effective_status (already computed per-schedule threshold)
    stale = [r for r in records if r.get("log_status") in ("STALE", "MISSING") and r.get("loaded")]

    lines += [
        "## Summary",
        f"- **Total plists:** {total}",
        f"- **Loaded (active):** {loaded_count}",
        f"- **Not loaded:** {unloaded_count}",
        f"- **🔴 DANGER (sends, no kill-switch):** {len(danger)}",
        f"- **🟡 WATCH (sends, has kill-switch):** {len(watch)}",
        f"- **💀 BROKEN (script missing):** {len(broken)}",
        f"- **Stale/silent logs (loaded but not running):** {len(stale)}",
        "",
    ]

    # DB outbound stats
    if db_stats:
        lines += ["## Outbound DB Stats (last 24h)", ""]
        lines += ["| Channel | Last 24h sent | Queued | Failed (all-time) |",
                  "|---------|--------------|--------|-------------------|"]
        for ch, st in sorted(db_stats.items()):
            lines.append(f"| {ch} | {st['last_24h']} | {st['queued']} | {st['failed']} |")
        lines += [""]

    # Danger + Watch section
    for risk_tag, label_str in [("DANGER", "🔴 DANGER — Sends without kill-switch"),
                                  ("WATCH", "🟡 WATCH — Sends, kill-switch present"),
                                  ("BROKEN", "💀 BROKEN — Script missing"),]:
        group = [r for r in records if r.get("risk") == risk_tag]
        if not group:
            continue
        lines += [f"## {label_str}", ""]
        for r in group:
            emoji = RISK_EMOJI.get(r["risk"], "")
            lines += [
                f"### {emoji} `{r['label']}`",
                f"- **Schedule:** {r.get('schedule','?')}",
                f"- **Script:** `{r.get('prog','?')[:100]}`",
                f"- **Log:** {r.get('log_status','?')} ({r.get('log_mtime','')}, {r.get('log_lines',0)} lines)",
                f"- **Send patterns:** {', '.join(r.get('send_patterns',[]) or ['none'])}",
                f"- **Kill-switches found:** {', '.join(r.get('kill_switches',[]) or ['NONE'])}",
                f"- **Live flags active in env:** {', '.join(r.get('live_flags_set',[]) or ['none'])}",
                "",
            ]

    # Full table
    lines += ["## Full Cron Inventory", ""]
    lines += ["| Risk | Label | Schedule | Log | Kill-switch | Sends? |",
              "|------|-------|----------|-----|-------------|--------|"]
    for r in records:
        emoji = RISK_EMOJI.get(r.get("risk", "SAFE"), "")
        risk = r.get("risk", "?")
        lbl = r.get("label", "?")
        sched = r.get("schedule", "?")
        log = f"{r.get('log_status','?')} {r.get('log_mtime','')}"
        ks = "✅" if r.get("kill_switches") else "❌"
        sends = "YES" if r.get("send_patterns") else "no"
        lines.append(f"| {emoji}{risk} | `{lbl}` | {sched} | {log} | {ks} | {sends} |")

    lines += [""]

    # Recommendations
    recs_section = ["## Recommendations", ""]
    recs_section += [
        "### Immediate action required",
        "",
    ]
    for r in danger:
        recs_section.append(f"- **🔴 `{r['label']}`** — sends `{', '.join(r.get('send_patterns',[]))}` with no kill-switch. "
                             f"Review script; add RICK_*_LIVE gate or unload until safe.")
    for r in broken:
        recs_section.append(f"- **💀 `{r['label']}`** — script missing, always exits error. "
                             f"Unload with `launchctl unload ~/Library/LaunchAgents/{r['label']}.plist`")

    recs_section += [
        "",
        "### Candidate unloads (stale / no-op / test)",
        "",
    ]
    no_ops = [r for r in records if r.get("loaded") and
              (r.get("log_status") in ("STALE","MISSING") or
               all("skip" in str(r) for _ in [1]))]
    for r in stale:
        recs_section.append(f"- `{r['label']}` — log {r.get('log_status')} since {r.get('log_mtime')}. "
                             f"Verify or unload.")
    recs_section += [""]

    lines += recs_section
    lines += [f"\n---\n*Generated by scripts/cron-audit.py at {now}*"]
    return "\n".join(lines)


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Rick LaunchAgent forensic auditor")
    ap.add_argument("--json", action="store_true", help="Output JSON instead of markdown")
    ap.add_argument("--md", metavar="FILE", help="Write markdown report to FILE")
    args = ap.parse_args()

    records, db_stats = audit()

    if args.json:
        print(json.dumps({"records": records, "db_stats": db_stats}, indent=2, default=str))
        return

    md = render_markdown(records, db_stats)

    if args.md:
        Path(args.md).write_text(md)
        print(f"Report written to {args.md}")
    else:
        print(md)


if __name__ == "__main__":
    main()
