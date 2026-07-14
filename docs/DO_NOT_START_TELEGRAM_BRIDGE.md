# ⛔ DO NOT START THE CUSTOM TELEGRAM BRIDGE

The custom `runtime/runner.py telegram-bridge` is PERMANENTLY DISABLED.

OpenClaw's built-in Telegram channel handles ALL Telegram communication.
Starting the custom bridge causes 409 conflicts that break Telegram completely.

## What was removed (2026-03-13):
- LaunchAgent: `ai.rick.telegram-bridge.plist` — DELETED
- Watchdog: disabled in both workspace and clawd configs
- tmux session: `rick-telegram` — killed and not to be recreated

## If you see "rick-telegram is down":
That is CORRECT and EXPECTED. Do NOT restart it. OpenClaw handles Telegram.

## Vlad's direct order:
"delete for forever custom rick-telegram bridge and focus only with openclaw integration"
