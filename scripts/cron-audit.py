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
    str(Path.home() / "rick-install-test" / "data" / "runtime" / "rick-runtime.db")))

# Patterns that indicate external send capability
SEND_PATTERNS = [
    r"resend", r"smtp", r"sendmail", r"send_email", r"requests\.post",
    r"outbound_dispatcher", r"fan_out", r"moltbook", r"linkedin.*post",
    r"instagram.*post", r"threads.*post", r"reddit.*post", r"AirDrop",
    r"airdrop", r"stripe.*charge", r"elevenlabs", r"twilio",
]

# Known kill-switch env flags (complete)
KILL_SWITCH_PATTERNS = [
    r"RICK_\w+_LIVE", r"DRY_RUN", r"--dry-run", r"--live",
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


def scan_script_body(prog_str: str) -> tuple[list[str], list[str], bool]:
    """
    Returns (kill_switches_found, send_patterns_found, script_exists).
    Scans the first resolved Python/shell script path in the prog string.
    """
    # Find the script path(s)
    paths = re.findall(r"(/[\w./\-]+\.(?:py|sh))", prog_str)
    kill_found = []
    send_found = []
    exists = False

    for script_path in paths:
        p = Path(script_path)
        if not p.exists():
            continue
        exists = True
        try:
            body = p.read_text(errors="replace")
        except Exception:
            continue
        for pattern in KILL_SWITCH_PATTERNS:
            hits = re.findall(pattern, body)
            for h in hits:
                if h not in kill_found:
                    kill_found.append(h)
        for pattern in SEND_PATTERNS:
            if re.search(pattern, body, re.IGNORECASE):
                label = pattern.replace(r"\.", ".").replace(r"\w+", "*").replace(r"\s*", "")
                if label not in send_found:
                    send_found.append(label)

    return kill_found, send_found, exists


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
    has_global_kill = any(
        k in kills for k in ["RICK_OUTBOUND_ENABLED", "DRY_RUN"] or
        any("RICK_" in k and "_LIVE" in k for k in kills)
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
        kill_switches, send_patterns, script_exists = scan_script_body(prog)
        is_loaded = label in loaded
        pid = pids.get(label, "-")
        disabled = p.get("Disabled", False)

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
            "log_status": log_status,
            "log_lines": log_lines,
            "log_mtime": log_mtime,
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
