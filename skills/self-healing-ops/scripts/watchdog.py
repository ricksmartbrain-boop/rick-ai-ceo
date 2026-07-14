#!/usr/bin/env python3
"""Managed watchdog with safe auto-restart policies for Rick."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[3]
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
SOCKET_PATH = Path(os.getenv("RICK_TMUX_SOCKET_PATH", str(Path.home() / ".tmux" / "sock")))
PROCESS_CONFIG_FILE = Path(
    os.path.expanduser(
        os.getenv("RICK_WATCHDOG_PROCESSES_FILE", str(ROOT_DIR / "config" / "watchdog-processes.json"))
    )
)
STATE_FILE = Path(
    os.path.expanduser(os.getenv("RICK_WATCHDOG_STATE_FILE", str(DATA_ROOT / "operations" / "watchdog-state.json")))
)
REPORT_FILE = DATA_ROOT / "control" / "watchdog-report.md"
RECOVERY_LOG_FILE = DATA_ROOT / "control" / "recovery-log.md"


@dataclass
class WatchdogResult:
    name: str
    kind: str
    status: str
    action: str
    details: str


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def expand_value(value: str) -> str:
    expanded = value.replace("$RICK_DATA_ROOT", str(DATA_ROOT)).replace("$HOME", str(Path.home()))
    return os.path.expandvars(os.path.expanduser(expanded))


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_state() -> dict[str, Any]:
    return load_json(STATE_FILE)


def save_state(state: dict[str, Any]) -> None:
    save_json(STATE_FILE, state)


def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_processes() -> list[dict[str, Any]]:
    payload = load_json(PROCESS_CONFIG_FILE)
    processes = payload.get("processes", [])
    return processes if isinstance(processes, list) else []


def check_tmux_session(session_name: str) -> tuple[bool, str]:
    result = run_command(["tmux", "-S", str(SOCKET_PATH), "has-session", "-t", session_name])
    return result.returncode == 0, session_name


def check_process(match: str) -> tuple[bool, str]:
    result = run_command(["pgrep", "-f", match])
    detail = result.stdout.strip() or match
    return result.returncode == 0, detail


def process_healthy(entry: dict[str, Any]) -> tuple[bool, str]:
    kind = entry.get("kind", "process")
    if kind == "tmux":
        return check_tmux_session(str(entry.get("session_name", entry.get("name", ""))).strip())
    return check_process(str(entry.get("match", entry.get("name", ""))).strip())


def can_restart(entry: dict[str, Any], state: dict[str, Any]) -> tuple[bool, str]:
    name = str(entry.get("name", "")).strip()
    cooldown_seconds = int(entry.get("cooldown_seconds", 180) or 180)
    max_restarts = int(entry.get("max_restarts_per_day", 12) or 12)
    item = state.get(name, {})
    daily_counts = item.get("daily_counts", {})
    today = today_key()
    if int(daily_counts.get(today, 0)) >= max_restarts:
        return False, "daily restart limit reached"
    last_restart_at = item.get("last_restart_at", "")
    if last_restart_at:
        try:
            last_restart = datetime.fromisoformat(last_restart_at)
            if (datetime.now() - last_restart).total_seconds() < cooldown_seconds:
                return False, "cooldown active"
        except ValueError:
            pass
    return True, ""


def mark_restart(name: str, state: dict[str, Any], status: str) -> None:
    item = state.setdefault(name, {})
    daily_counts = item.setdefault("daily_counts", {})
    today = today_key()
    daily_counts[today] = int(daily_counts.get(today, 0)) + 1
    item["last_restart_at"] = now_iso()
    item["last_restart_status"] = status


def execute_restart(entry: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    restart_cmd = expand_value(str(entry.get("restart_cmd", "")).strip())
    if not restart_cmd:
        return subprocess.CompletedProcess([], 1, stdout="", stderr="missing restart_cmd")

    if entry.get("kind") == "tmux":
        session_name = str(entry.get("session_name", entry.get("name", ""))).strip()
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        return run_command(["tmux", "-S", str(SOCKET_PATH), "new", "-d", "-s", session_name, restart_cmd])
    return run_command(["bash", "-lc", restart_cmd])


def append_recovery_log(result: WatchdogResult) -> None:
    RECOVERY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not RECOVERY_LOG_FILE.exists():
        RECOVERY_LOG_FILE.write_text("# Recovery Log\n\n", encoding="utf-8")
    with RECOVERY_LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(
            f"- {datetime.now():%Y-%m-%d %H:%M} | {result.name} | {result.status} | {result.action} | {result.details}\n"
        )


def render_report(results: list[WatchdogResult]) -> str:
    lines = [
        "# Watchdog Report",
        "",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "| Name | Kind | Status | Action | Details |",
        "|------|------|--------|--------|---------|",
    ]
    for item in results:
        lines.append(f"| {item.name} | {item.kind} | {item.status} | {item.action} | {item.details} |")
    if not results:
        lines.append("| none | none | warn | none | no watchdog processes configured |")
    return "\n".join(lines) + "\n"


def write_report(results: list[WatchdogResult]) -> None:
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(render_report(results), encoding="utf-8")


def sanitize_detail(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    return cleaned.replace("|", "/")[:240] if cleaned else "ok"


def run_watchdog() -> list[WatchdogResult]:
    state = load_state()
    results: list[WatchdogResult] = []

    for entry in load_processes():
        if not entry.get("enabled", True):
            continue
        name = str(entry.get("name", "unnamed")).strip() or "unnamed"
        kind = str(entry.get("kind", "process")).strip() or "process"
        healthy, detail = process_healthy(entry)
        if healthy:
            results.append(WatchdogResult(name, kind, "pass", "none", sanitize_detail(detail)))
            continue

        if not str(entry.get("restart_cmd", "")).strip():
            result = WatchdogResult(name, kind, "fail", "none", "missing restart command")
            append_recovery_log(result)
            results.append(result)
            continue

        allowed, reason = can_restart(entry, state)
        if not allowed:
            result = WatchdogResult(name, kind, "warn", "skipped", reason)
            append_recovery_log(result)
            results.append(result)
            continue

        restart = execute_restart(entry)
        healthy_after, after_detail = process_healthy(entry)
        if restart.returncode == 0 and healthy_after:
            mark_restart(name, state, "restarted")
            result = WatchdogResult(name, kind, "pass", "restarted", sanitize_detail(after_detail))
        else:
            mark_restart(name, state, "restart-failed")
            details = restart.stderr.strip() or restart.stdout.strip() or after_detail or "restart failed"
            result = WatchdogResult(name, kind, "fail", "restart-failed", sanitize_detail(details))
        append_recovery_log(result)
        results.append(result)

    save_state(state)
    write_report(results)
    return results


def main() -> int:
    results = run_watchdog()
    print(render_report(results).rstrip())
    return 0 if all(result.status in {"pass", "warn"} for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
