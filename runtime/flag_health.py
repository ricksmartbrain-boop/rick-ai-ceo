"""Per-flag staleness probes for LIVE env flags.

Reads the most recent successful activity timestamp from each flag's known
log file. Surfaces staleness in the daily digest so silent regressions
(flag ON but loop dead) get caught within the staleness window.

This is observation-only: never flips flags, never restarts services. It
exists to surface the "X silent for 4 days" class of bug that previously
required Vlad to diagnose by hand.
"""
from __future__ import annotations

import glob as _glob
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS = DATA_ROOT / "operations"

# (flag_name, log_relative_path, max_age_hours, optional_filter_lambda)
# max_age_hours: how stale before the flag is "stale" (red in digest)
# filter_lambda(entry_dict) → bool: True if entry counts as a successful run.
#   Default (None) treats any entry as success.
FLAG_PROBES: list[tuple[str, str, float, Optional[Callable[[dict], bool]]]] = [
    # FENIX filter: only count live-mode decisions. Without this, observe-mode
    # entries (written when RICK_FENIX_LIVE=0) would mask a silently-broken
    # live-mode pipeline because someone else keeps writing to the same log.
    ("RICK_FENIX_LIVE", "fenix-decisions.jsonl", 26.0,
        lambda e: e.get("mode") == "live"),
    ("RICK_HIVE_ENABLED", "hive-heartbeat.jsonl", 1.5,
        lambda e: e.get("result") == "posted" or e.get("status") == "ok"),
    # 26h not 8h: hive-sync's actual cadence is daily (04:30 PT per
    # ai.rick.hive-sync.plist), not every 6h as I originally assumed. 26h
    # gives 2h slack on a 24h cron. Confirmed via Rick TUI 2026-04-25.
    ("RICK_HIVE_SYNC_LIVE", "hive-sync.jsonl", 26.0,
        lambda e: e.get("status") == "ok"),
    ("RICK_VARA_LIVE", "vara.jsonl", 168.0, None),
    # 170h not 26h: lead-replay runs weekly (Mondays 09:00 PT per
    # ai.rick.lead-replay.plist StartCalendarInterval). 26h flapped between
    # cycles. 170h gives 2h slack on the 168h cadence.
    ("RICK_LEAD_REPLAY_LIVE", "lead-replay.jsonl", 170.0, None),
    ("RICK_IMAP_LIVE", "imap-watcher.jsonl", 1.0,
        lambda e: e.get("status") == "ok"),
    ("RICK_REPLY_ROUTER_LIVE", "reply-router.jsonl", 26.0, None),
    ("RICK_OUTBOUND_MOLTBOOK_LIVE", "outbound-dispatcher.jsonl", 26.0,
        lambda e: e.get("channel") == "moltbook"
        and e.get("status") in ("sent", "observed-only")),
    # campaign-engine.py calls Resend directly and appends to email-sends.jsonl
    # (ops surface added 2026-04-27). email-sequence-send.py also writes there
    # via its own append_log. 26h gives 2h slack on the daily send cadence.
    ("RICK_EMAIL_SEND_LIVE", "email-sends.jsonl", 26.0,
        lambda e: e.get("status") == "sent"),
    ("RICK_OUTBOUND_THREADS_LIVE", "outbound-dispatcher.jsonl", 26.0,
        lambda e: e.get("channel") == "threads"
        and e.get("status") in ("sent", "observed-only")),
    ("RICK_OUTBOUND_INSTAGRAM_LIVE", "outbound-dispatcher.jsonl", 26.0,
        lambda e: e.get("channel") == "instagram"
        and e.get("status") in ("sent", "observed-only")),
    ("RICK_ANALYTICS_LIVE", "analytics-ingest.jsonl", 26.0, None),
    # Added 2026-04-26 after Rick TUI surfaced "CDP port 9225 dead" — LinkedIn
    # outbound was silently muted (chrome-cdp-linkedin LaunchAgent had stopped
    # without crash, KeepAlive only triggers on Crashed=true). Probe surfaces
    # any future repeat so a restart can be triggered before backlog grows.
    ("RICK_OUTBOUND_LINKEDIN_LIVE", "outbound-dispatcher.jsonl", 26.0,
        lambda e: e.get("channel") == "linkedin"
        and e.get("status") in ("sent", "observed-only")),
    # feed-poll runs hourly (ai.rick.feed-poll.plist StartInterval=3600).
    # 2h = one-cycle slack. Fixed OPS path; filter on terminal run.done event
    # so partial/interrupted runs don't mask a stalled loop.
    ("RICK_FEED_POLL_LIVE", "feed-poll.jsonl", 2.0,
        lambda e: e.get("event") == "run.done"),
    # daily-proof-engine runs daily at 09:00 PT (ai.rick.daily-proof-engine.plist
    # StartCalendarInterval Hour=9 Minute=0). Filter on the terminal run.done
    # event so generate-only failures don't look fresh.
    ("RICK_DAILY_PROOF_LIVE", "daily-proof-engine.jsonl", 26.0,
        lambda e: e.get("event") == "run.done"),
    # resend-bounce-poll runs every 5 min (ai.rick.resend-bounce-poll.plist
    # StartInterval=300). Writes a poll.done sentinel on every run (even when
    # no bounces found) so the probe detects a dead loop within 1h.
    # 1.0h = 12 missed cycles before alerting — enough hysteresis for machine
    # sleep without false positives.
    ("RICK_BOUNCE_POLL_LIVE", "email-bounces.jsonl", 1.0,
        lambda e: e.get("event") == "poll.done"),
    # bounce-rate-guardian runs hourly (ai.rick.bounce-rate-guardian.plist,
    # StartInterval=3600). Writes a run.done sentinel on every pass so the
    # guard itself trips if it goes stale.
    ("RICK_BOUNCE_GUARDIAN_LIVE", "bounce-rate-guardian.jsonl", 2.0,
        lambda e: e.get("event") == "run.done"),
    # roast-lead-poll runs every 5 min (ai.rick.roast-lead-poll.plist
    # StartInterval=300). 0.5h = 6 missed cycles before alerting — enough
    # hysteresis to survive a brief machine sleep without false positives.
    # run.start + run.no_leads both confirm a completed poll cycle.
    ("RICK_ROAST_LEAD_POLL_LIVE", "roast-lead-poll.jsonl", 0.5,
        lambda e: e.get("event", "").startswith("run.")),
    # founder-graph runs daily at 04:00 PT (ai.rick.founder-graph.plist
    # StartCalendarInterval Hour=4). Logs to date-rotating
    # ~/rick-vault/data/founder-graph-YYYY-MM-DD.jsonl; glob in scan_flags()
    # resolves to the most-recent file. 26h = 2h slack on a 24h cadence.
    ("RICK_FOUNDER_GRAPH_LIVE", "../data/founder-graph-*.jsonl", 26.0,
        lambda e: e.get("action") in ("upsert", "would-upsert")),
    # whois-firehose runs daily at 03:15 PT (ai.rick.whois-firehose.plist
    # StartCalendarInterval Hour=3 Minute=15). Date-rotating log same dir as
    # founder-graph above; 26h slack on 24h cadence, same rationale.
    ("RICK_WHOIS_LIVE", "../data/whois-firehose-*.jsonl", 26.0,
        lambda e: e.get("domain") is not None),
    # google-maps-firehose has no LaunchAgent — runs manually / ad-hoc.
    # Last log: 2026-04-22 (CDP captcha hit that day). 168h (7-day window)
    # avoids permanently red-lining a probe for an infrequently-run job.
    # Filter excludes captcha/error outcomes so only genuine scrape activity
    # resets the clock; error entries alone would mask a broken setup.
    ("RICK_GOOGLE_MAPS_LIVE", "../data/google-maps-firehose-*.jsonl", 168.0,
        lambda e: e.get("outcome") in ("inserted", "duplicate-or-bad")),
    # gmail_personal: sequencer Day-5 / Day-15 personal-touch slots.
    # Low-cadence by design (daily=5). 48h window = acceptable if no touch
    # needed today; staleness only fires if sequencer has due slots but
    # the channel went silent (auth failure, formatter crash, etc.).
    # Shares outbound-dispatcher.jsonl with other channels.
    ("RICK_GMAIL_PERSONAL_LIVE", "outbound-dispatcher.jsonl", 48.0,
        lambda e: e.get("channel") == "gmail_personal"
        and e.get("status") == "sent"),
]


def _parse_ts(entry: dict) -> Optional[datetime]:
    raw = entry.get("ts") or entry.get("ran_at") or entry.get("timestamp")
    if not raw:
        return None
    try:
        # Python <3.11 fromisoformat rejects "Z" suffix; normalise to +00:00.
        if isinstance(raw, str) and raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _iter_jsonl_reverse(path: Path, max_lines: int = 5000):
    """Yield parsed JSON entries from end of file. Bounded for big logs.

    max_lines caps memory at ~5K * line_size; sufficient for most flags
    since we stop at the first matching entry.
    """
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    lines = text.splitlines()
    for line in reversed(lines[-max_lines:]):
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _flag_is_on(flag: str) -> bool:
    val = os.getenv(flag, "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def scan_flags() -> list[dict]:
    """Return per-flag status records.

    Each record: {flag, on, last_success_ts, age_hours, status,
                  log_path, max_age_hours}
    status ∈ {"fresh", "stale", "no_data", "off"}.
    """
    results = []
    now = datetime.now()
    for flag, rel_path, max_age_h, filt in FLAG_PROBES:
        log_path = OPS / rel_path
        # Resolve glob patterns (date-rotating logs like ../data/NAME-YYYY-MM-DD.jsonl).
        # Pick the lexicographically-last match so YYYY-MM-DD ordering selects
        # the most recent file without importing dateutil.
        if "*" in rel_path:
            matches = sorted(_glob.glob(str(log_path)))
            if matches:
                log_path = Path(matches[-1])
        on = _flag_is_on(flag)
        record = {
            "flag": flag,
            "on": on,
            "last_success_ts": None,
            "age_hours": None,
            "status": "off" if not on else "no_data",
            "log_path": str(log_path),
            "max_age_hours": max_age_h,
        }
        if not on:
            results.append(record)
            continue
        latest = None
        for entry in _iter_jsonl_reverse(log_path):
            if filt is not None:
                try:
                    if not filt(entry):
                        continue
                except Exception:
                    continue
            ts = _parse_ts(entry)
            if ts is not None:
                latest = ts
                break
        if latest is None:
            results.append(record)
            continue
        # Strip tz on the parsed ts so subtraction with naive `now` doesn't
        # raise when a logger writes `2026-04-25T...+00:00`. Time math is
        # local-clock for both sides; logs are wall-clock anyway.
        if latest.tzinfo is not None:
            # 2026-04-27: tz-aware logs (UTC with +00:00 / Z suffix) were being
            # stripped to naive then compared against naive local now() →
            # negative ages (-7h on PDT). astimezone() without arg converts
            # to system local first, THEN strip — so the comparison is local
            # vs local, age math is correct.
            latest = latest.astimezone().replace(tzinfo=None)
        try:
            age_h = (now - latest).total_seconds() / 3600.0
        except TypeError:
            results.append(record)
            continue
        record["last_success_ts"] = latest.isoformat(timespec="seconds")
        record["age_hours"] = round(age_h, 2)
        record["status"] = "fresh" if age_h <= max_age_h else "stale"
        results.append(record)
    return results


def stale_flags() -> list[dict]:
    return [r for r in scan_flags() if r["status"] == "stale"]


if __name__ == "__main__":
    import sys
    for r in scan_flags():
        flag, status, age = r["flag"], r["status"], r["age_hours"]
        age_str = f"{age:.1f}h" if age is not None else "-"
        sys.stdout.write(f"{status:>8}  {flag:<32} {age_str}\n")
