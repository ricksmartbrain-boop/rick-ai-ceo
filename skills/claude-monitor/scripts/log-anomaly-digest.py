#!/usr/bin/env python3
"""Scan daemon and cron logs for anomalies and write a digest."""

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", Path.home() / "rick-vault"))
OUTPUT_MD = DATA_ROOT / "dashboards" / "log-anomalies.md"
OUTPUT_JSONL = DATA_ROOT / "operations" / "log-anomalies.jsonl"

WINDOW_HOURS = 6
PATTERN = re.compile(r"\b(ERROR|WARN(?:ING)?|FAIL(?:ED)?|Traceback|Exception)\b", re.IGNORECASE)
CONTEXT_LINES = 2


def ensure_parent(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def collect_log_files() -> list[Path]:
    logs = []
    daemon_log = DATA_ROOT / "logs" / "daemon.log"
    if daemon_log.exists():
        logs.append(daemon_log)
    cron_dir = DATA_ROOT / "logs" / "cron"
    if cron_dir.is_dir():
        logs.extend(sorted(cron_dir.glob("*.log")))
    return logs


def scan_file(path: Path, cutoff: float) -> list[dict]:
    hits = []
    try:
        mtime = path.stat().st_mtime
        if mtime < cutoff:
            return []
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []

    for i, line in enumerate(lines):
        m = PATTERN.search(line)
        if m:
            severity = m.group(1).upper()
            if severity.startswith("WARN"):
                severity = "WARN"
            if severity.startswith("FAIL"):
                severity = "FAIL"
            start = max(0, i - CONTEXT_LINES)
            end = min(len(lines), i + CONTEXT_LINES + 1)
            context = "\n".join(lines[start:end])
            hits.append({
                "file": str(path.name),
                "line": i + 1,
                "severity": severity,
                "text": line.strip()[:200],
                "context": context[:500],
            })
    return hits


def load_previous_count() -> int:
    if not OUTPUT_JSONL.exists():
        return 0
    try:
        last_line = ""
        for line in OUTPUT_JSONL.read_text().splitlines():
            if line.strip():
                last_line = line
        if last_line:
            return json.loads(last_line).get("total", 0)
    except (json.JSONDecodeError, OSError):
        pass
    return 0


def build_digest(hits: list[dict], prev_count: int) -> str:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = len(hits)

    severity_counts = Counter(h["severity"] for h in hits)
    trend = total - prev_count
    trend_str = f"+{trend}" if trend >= 0 else str(trend)

    md = (
        f"---\nupdated: {now_str}\ntype: log-anomalies\n---\n\n"
        f"# Log Anomalies (last {WINDOW_HOURS}h)\n\n"
        f"- **Total hits:** {total} (trend: {trend_str} vs previous window)\n"
    )

    if severity_counts:
        md += "\n| Severity | Count |\n|----------|-------|\n"
        for sev in ["ERROR", "FAIL", "WARN", "Traceback", "Exception"]:
            if sev in severity_counts:
                md += f"| {sev} | {severity_counts[sev]} |\n"

    recent = hits[-5:] if hits else []
    if recent:
        md += "\n## Top 5 Recent Errors\n\n"
        for h in reversed(recent):
            md += f"### {h['file']}:{h['line']} ({h['severity']})\n"
            md += f"```\n{h['context']}\n```\n\n"
    else:
        md += "\nNo anomalies detected.\n"

    return md


def main() -> None:
    cutoff = time.time() - WINDOW_HOURS * 3600
    log_files = collect_log_files()

    all_hits: list[dict] = []
    for lf in log_files:
        all_hits.extend(scan_file(lf, cutoff))

    prev_count = load_previous_count()
    md = build_digest(all_hits, prev_count)

    ensure_parent(OUTPUT_MD)
    OUTPUT_MD.write_text(md)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "ts": now_str,
        "total": len(all_hits),
        "by_severity": dict(Counter(h["severity"] for h in all_hits)),
        "files_scanned": len(log_files),
    }
    ensure_parent(OUTPUT_JSONL)
    with OUTPUT_JSONL.open("a") as f:
        f.write(json.dumps(record) + "\n")

    print(f"log-anomaly-digest: wrote {OUTPUT_MD} ({len(all_hits)} hits)")


if __name__ == "__main__":
    main()
