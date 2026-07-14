---
name: agent-ops-playbook
description: Diagnose stuck agents, restart failed processes, manage tmux sessions, run health checks, and resolve common operational failures.
---

# Agent Ops Playbook

Use this playbook whenever automation appears stalled, degraded, or broken.

## 1) Diagnose Stuck Agents

### Fast Triage
```bash
ps aux | rg "openclaw|codex|worker|scheduler|telegram-bridge|run-daemon"
tmux -S ~/.tmux/sock list-sessions
```

### Check Progress vs Stall
```bash
tmux -S ~/.tmux/sock capture-pane -t <session> -p | tail -40
```

Stall indicators:
- same log output across multiple checks
- no CPU usage and no new log lines
- repeating recoverable error with no backoff

## 2) Restart Crashed Processes

### Single Process Restart
```bash
pkill -f "<pattern>" || true
nohup <command> >> ~/rick-vault/logs/<name>.log 2>&1 &
```

### tmux Session Restart
```bash
tmux -S ~/.tmux/sock kill-session -t <session> || true
tmux -S ~/.tmux/sock new -d -s <session> "cd <repo> && <command>; echo EXIT:$?; sleep 999999"
```

## 3) tmux Management Standards
- always use stable socket: `~/.tmux/sock`
- use named sessions per workload (`inbox`, `builder`, `research`, `distribution`)
- keep completion tail (`echo EXIT:$?; sleep 999999`) for postmortem visibility

## 4) Health Check Procedure

### Standard Run
```bash
bash scripts/health-check.sh -t ~/.config/openclaw/health-targets.conf --verbose
bash skills/self-healing-ops/scripts/watchdog.sh
```

### If Failing
1. identify the first failing dependency
2. restart only the failing component
3. re-run health checks
4. emit an OpenClaw system event with root cause and fix status

## 5) Log Analysis Patterns

Look for:
- auth failures (`401`, `403`, token expired)
- network failures (DNS, timeout, TLS)
- rate limiting (`429`)
- resource exhaustion (OOM, file descriptor limits, disk full)

Useful commands:
```bash
tail -n 200 ~/rick-vault/logs/<service>.log
rg -n "ERROR|WARN|429|timeout|ECONN|auth" ~/rick-vault/logs/<service>.log
```

## 6) Common Failure Modes

- missing credentials
- cron not firing
- zombie worker with no progress
- model provider quota exhaustion
- stuck approval queue

## 7) Escalation Criteria

Escalate immediately when:
- customer-facing production is down and cannot be restored quickly
- data integrity is at risk
- security incident is suspected
- repeated crash loops persist after one restart cycle
