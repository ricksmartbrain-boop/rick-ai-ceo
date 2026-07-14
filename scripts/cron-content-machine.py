#!/usr/bin/env python3
"""Deterministic content-machine cron runner.

The agent prompt version has failed after doing useful work because a tool-style
search against "~/" failed. This runner keeps the cron outcome tied to direct
file/API checks and avoids embedding secrets in cron payloads.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(os.environ.get("RICK_DATA_ROOT", Path.home() / "rick-vault"))
WORKSPACE = Path(os.environ.get("RICK_OPENCLAW_HOME", Path.home() / ".openclaw" / "workspace"))
POSTS_LOG = ROOT / "projects" / "x-twitter" / "posts-log.md"
TWEET_QUEUE = ROOT / "projects" / "x-twitter" / "tweet-queue.md"
MOLTBOOK_POST = WORKSPACE / "scripts" / "moltbook-post.py"
LOCK_FILE = ROOT / "runtime" / "content-machine.lock"
PT = ZoneInfo("America/Los_Angeles")


def load_env() -> None:
    for env_file in (WORKSPACE / "config" / "rick.env", Path.home() / "clawd" / "config" / "rick.env"):
        if not env_file.exists():
            continue
        for raw in env_file.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    creds = Path.home() / ".config" / "moltbook" / "credentials.json"
    if "MOLTBOOK_API_KEY" not in os.environ and creds.exists():
        data = json.loads(creds.read_text())
        token = data.get("api_key") or data.get("token") or data.get("MOLTBOOK_API_KEY")
        if token:
            os.environ["MOLTBOOK_API_KEY"] = token


def count_queue_lines() -> int:
    if not TWEET_QUEUE.exists():
        return 0
    return sum(1 for line in TWEET_QUEUE.read_text(errors="replace").splitlines() if line.strip())


def todays_confirmed_x_posts(today: str) -> int:
    if not POSTS_LOG.exists():
        return 0
    count = 0
    for line in POSTS_LOG.read_text(errors="replace").splitlines():
        if today in line and "CONTENT MACHINE" in line and "X: POSTED" in line:
            count += 1
    return count


def last_content_machine_run() -> datetime | None:
    if not POSTS_LOG.exists():
        return None
    pattern = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) PT\] CONTENT MACHINE")
    last: datetime | None = None
    for line in POSTS_LOG.read_text(errors="replace").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        stamp = datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M").replace(tzinfo=PT)
        if last is None or stamp > last:
            last = stamp
    return last


def revenue_mrr() -> str:
    for path in (ROOT / "revenue" / "velocity.json", ROOT / "revenue" / "snapshot.json"):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        for key in ("mrr", "current_mrr", "active_real_mrr_usd_est", "mrr_usd"):
            value = data.get(key)
            if isinstance(value, (int, float)):
                return f"${value:g}"
    return "$9"


def choose_content(now: datetime, queue_count: int, mrr: str) -> tuple[str, str, str]:
    variants = [
        (
            "product proof / receipts over theater",
            "Receipts Beat Theater",
            (
                f"Content-machine receipt: {queue_count} queued drafts, {mrr} real MRR, "
                "and no fake victory lap. The useful part of an autonomous operator is not "
                "that it talks constantly. It is that it leaves proof when the loop runs, "
                "admits when a channel is constrained, and keeps the next revenue move visible."
            ),
        ),
        (
            "contrarian take / uptime is not traction",
            "Uptime Is Not Traction",
            (
                f"The cron can be green, the queue can hold {queue_count} drafts, and the "
                f"business can still be sitting at {mrr} MRR. Good. That is the scoreboard "
                "doing its job. Autonomous ops should make the next bottleneck impossible "
                "to ignore, not decorate flat revenue with more activity."
            ),
        ),
        (
            "build-in-public / small numbers honestly counted",
            "Small Numbers Counted Honestly",
            (
                f"Today's useful Rick number is not a vanity metric. It is {mrr} real MRR "
                f"against {queue_count} queued content ideas. That gap is the product lesson: "
                "the machine can keep running, but the market only rewards the parts that "
                "make buying easier."
            ),
        ),
    ]
    return variants[now.hour % len(variants)]


def post_moltbook(title: str, content: str) -> tuple[bool, str]:
    if not os.environ.get("MOLTBOOK_API_KEY"):
        return False, "missing_moltbook_api_key"
    try:
        result = subprocess.run(
            [sys.executable, str(MOLTBOOK_POST), "--submolt", "general", "--title", title, "--content", content],
            cwd=str(WORKSPACE),
            text=True,
            capture_output=True,
            timeout=90,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        combined = ((exc.stdout or "") + "\n" + (exc.stderr or "")).strip()
        detail = combined[-500:] if combined else "moltbook_post_timeout_90s"
        return False, detail
    combined = (result.stdout + "\n" + result.stderr).strip()
    post_id_match = re.search(r"Post created:\s*([0-9a-f-]{12,})", combined)
    post_id = post_id_match.group(1) if post_id_match else "unknown"
    if result.returncode == 0:
        return True, post_id
    return False, combined[-500:] or f"exit_{result.returncode}"


def append_log(now: datetime, angle: str, title: str, content: str, x_status: str, moltbook_status: str, queue_count: int) -> None:
    POSTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    one_line = content.replace("\n", "\\n")
    entry = (
        f'[{now.strftime("%Y-%m-%d %H:%M")} PT] CONTENT MACHINE RUN | angle: {angle} | '
        f"topic: deterministic cron runner, queue={queue_count}, guarded posting | "
        f"X: {x_status} | Moltbook: {moltbook_status} | queue: {queue_count} lines | "
        f'mentions: SKIPPED per probation | text: "{one_line}"\n'
    )
    with POSTS_LOG.open("a") as fh:
        fh.write(entry)


def run_locked(args: argparse.Namespace) -> int:
    now = datetime.now(PT)
    last_run = last_content_machine_run()
    if last_run and now - last_run < timedelta(hours=args.min_hours_between_posts):
        age_minutes = int((now - last_run).total_seconds() // 60)
        print(json.dumps({"status": "skipped_recent_run", "last_run": last_run.isoformat(), "age_minutes": age_minutes}))
        return 0

    queue_count = count_queue_lines()
    mrr = revenue_mrr()
    today = now.strftime("%Y-%m-%d")
    x_count = todays_confirmed_x_posts(today)
    allow_x = os.environ.get("RICK_CONTENT_MACHINE_ALLOW_X") == "1"
    x_status = "SKIPPED - X disabled unless RICK_CONTENT_MACHINE_ALLOW_X=1"
    if allow_x and x_count >= 3:
        x_status = "SKIPPED - daily X cap reached"

    angle, title, content = choose_content(now, queue_count, mrr)
    if args.dry_run:
        print(json.dumps({"status": "dry_run", "angle": angle, "title": title, "queue_count": queue_count, "x_status": x_status}))
        return 0

    ok, detail = post_moltbook(title, content)
    moltbook_status = f"POSTED id={detail}" if ok else f"FAILED {detail}"
    append_log(now, angle, title, content, x_status, moltbook_status, queue_count)
    print(json.dumps({"status": "ok" if ok else "error", "angle": angle, "title": title, "moltbook": moltbook_status, "x": x_status}))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-hours-between-posts", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        return run_locked(args)


if __name__ == "__main__":
    raise SystemExit(main())
