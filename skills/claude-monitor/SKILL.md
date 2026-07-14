# claude-monitor

System and service monitoring that writes dashboards and control files into the Obsidian vault.

## Scripts

| Script | Frequency | Trigger | Output |
|--------|-----------|---------|--------|
| `system-health.sh` | Every 30 min | `run-heartbeat.sh` | `control/system-health.md`, `operations/system-health.jsonl` |
| `openclaw-health.sh` | Every 30 min | `run-heartbeat.sh` | `control/openclaw-status.md` |
| `claude-session-digest.py` | Daily 3 AM | `run-nightly.sh` | `dashboards/claude-sessions.md` |
| `log-anomaly-digest.py` | Every 6 h | `run-log-digest.sh` | `dashboards/log-anomalies.md`, `operations/log-anomalies.jsonl` |

## Details

### system-health.sh
Collects disk usage (`df -h /`), memory pressure (`memory_pressure`), load average (`uptime`), and checks key processes (rick-daemon, openclaw, telegram-bridge) via `pgrep -f`. Writes a markdown table and appends a JSONL line for trend tracking.

### openclaw-health.sh
Checks gateway process, tails `~/.openclaw/logs/gateway.log` for recent errors/warnings, lists active agents from `~/.openclaw/agents/`, and counts recent Telegram sends. Gateway is WebSocket-based (no REST health endpoint).

### claude-session-digest.py
Parses `~/.claude/history.jsonl` (schema: `{display, timestamp, project, sessionId}`). Groups by session, filters last 24 h, reports session count, projects touched, and per-session summary.

### log-anomaly-digest.py
Scans `logs/daemon.log` and `logs/cron/*.log` for ERROR/WARN/FAIL/Traceback/Exception. Reports count by severity, top 5 recent errors with context, and trend vs previous window.
