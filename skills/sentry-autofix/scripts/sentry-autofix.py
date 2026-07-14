#!/usr/bin/env python3
"""Sentry → Codex → PR autofix pipeline.

Flow:
1. Receive Sentry issue (via webhook payload or CLI)
2. Triage: filter by severity, revenue impact, recency
3. Generate fix spec from stacktrace + context
4. Dispatch to Codex/Ralph coding agent
5. Create PR with fix
6. Notify via Telegram
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
AUTOFIX_DIR = DATA_ROOT / "operations" / "sentry-autofix"
ROOT_DIR = Path(__file__).resolve().parents[3]


def load_env():
    return {
        "sentry_token": os.getenv("SENTRY_AUTH_TOKEN", ""),
        "sentry_org": os.getenv("SENTRY_ORG", ""),
        "sentry_project": os.getenv("SENTRY_PROJECT", ""),
        "github_repo": os.getenv("SENTRY_GITHUB_REPO", ""),
        "codex_bin": os.getenv("RICK_CODEX_BIN", "codex"),
        "ralph_bin": os.getenv("RICK_RALPH_BIN", "ralphy"),
    }


def triage_issue(issue: dict) -> dict:
    """Score and triage a Sentry issue. Returns triage decision."""
    level = issue.get("level", "error")
    count = int(issue.get("count", 0))
    user_count = int(issue.get("userCount", 0))
    is_regression = issue.get("isRegression", False)
    title = issue.get("title", "")

    score = 0
    reasons = []

    # Severity scoring
    severity_scores = {"fatal": 40, "error": 25, "warning": 10, "info": 0}
    score += severity_scores.get(level, 10)
    reasons.append(f"severity={level}")

    # Volume scoring
    if count > 100:
        score += 20
        reasons.append(f"high-volume({count})")
    elif count > 10:
        score += 10
        reasons.append(f"moderate-volume({count})")

    # User impact
    if user_count > 10:
        score += 15
        reasons.append(f"multi-user-impact({user_count})")
    elif user_count > 0:
        score += 5

    # Regression bonus
    if is_regression:
        score += 20
        reasons.append("regression")

    # Revenue keywords
    revenue_keywords = ["checkout", "payment", "stripe", "purchase", "billing", "subscription"]
    if any(kw in title.lower() for kw in revenue_keywords):
        score += 25
        reasons.append("revenue-affecting")

    action = "autofix" if score >= 40 else "review" if score >= 20 else "monitor"

    return {
        "issue_id": issue.get("id", "unknown"),
        "title": title,
        "level": level,
        "score": score,
        "action": action,
        "reasons": reasons,
        "triaged_at": datetime.now().isoformat(timespec="seconds"),
    }


def generate_fix_spec(issue: dict, latest_event: dict | None = None) -> str:
    """Generate a fix specification from a Sentry issue."""
    title = issue.get("title", "Unknown error")
    stacktrace = ""

    if latest_event:
        for entry in latest_event.get("entries", []):
            if entry.get("type") == "exception":
                for value in entry.get("data", {}).get("values", []):
                    st = value.get("stacktrace", {})
                    for frame in st.get("frames", [])[-5:]:
                        stacktrace += f"  {frame.get('filename', '?')}:{frame.get('lineNo', '?')} in {frame.get('function', '?')}\n"

    spec = f"""## Fix Spec: {title}

**Sentry Issue:** #{issue.get('id', 'unknown')}
**Level:** {issue.get('level', 'error')}
**Occurrences:** {issue.get('count', 0)}
**Users affected:** {issue.get('userCount', 0)}

### Error
{title}

### Stacktrace (last 5 frames)
```
{stacktrace.strip() or 'No stacktrace available — check Sentry directly'}
```

### Instructions
1. Identify the root cause from the stacktrace
2. Write a minimal, targeted fix
3. Add a regression test if possible
4. Do NOT refactor surrounding code
5. Create a PR with a clear title referencing the Sentry issue
"""
    return spec


def dispatch_codex(fix_spec: str, repo: str, env: dict) -> dict:
    """Dispatch fix to Codex coding agent."""
    codex_bin = env["codex_bin"]

    try:
        result = subprocess.run(
            [codex_bin, "--quiet", "--prompt", fix_spec],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        return {
            "tool": "codex",
            "success": result.returncode == 0,
            "output": result.stdout[:2000],
            "error": result.stderr[:500] if result.returncode != 0 else "",
        }
    except FileNotFoundError:
        return {"tool": "codex", "success": False, "error": f"Codex not found at {codex_bin}"}
    except subprocess.TimeoutExpired:
        return {"tool": "codex", "success": False, "error": "Codex timed out after 300s"}


def dispatch_ralph(fix_spec: str, repo: str, env: dict) -> dict:
    """Dispatch fix to Ralph coding loop as fallback."""
    ralph_bin = env["ralph_bin"]

    try:
        result = subprocess.run(
            [ralph_bin, "run", "--task", fix_spec],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        return {
            "tool": "ralph",
            "success": result.returncode == 0,
            "output": result.stdout[:2000],
            "error": result.stderr[:500] if result.returncode != 0 else "",
        }
    except FileNotFoundError:
        return {"tool": "ralph", "success": False, "error": f"Ralph not found at {ralph_bin}"}
    except subprocess.TimeoutExpired:
        return {"tool": "ralph", "success": False, "error": "Ralph timed out after 600s"}


def create_pr(branch_name: str, title: str, body: str, repo: str) -> dict:
    """Create a GitHub PR using gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body, "--repo", repo],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0:
            return {"success": True, "url": result.stdout.strip()}
        return {"success": False, "error": result.stderr.strip()}
    except FileNotFoundError:
        return {"success": False, "error": "gh CLI not found"}


def notify_telegram(message: str):
    """Send notification via Telegram bridge."""
    telegram_script = ROOT_DIR / "scripts" / "telegram-command.sh"
    if telegram_script.exists():
        subprocess.run(
            ["bash", str(telegram_script), message],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


def process_webhook(payload: dict) -> dict:
    """Process an incoming Sentry webhook payload."""
    action = payload.get("action", "")
    data = payload.get("data", {})
    issue = data.get("issue", data)

    triage = triage_issue(issue)

    AUTOFIX_DIR.mkdir(parents=True, exist_ok=True)
    log_file = AUTOFIX_DIR / f"{triage['issue_id']}-{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    log_file.write_text(json.dumps({
        "webhook_action": action,
        "triage": triage,
        "issue_summary": {
            "id": issue.get("id"),
            "title": issue.get("title"),
            "level": issue.get("level"),
            "count": issue.get("count"),
        },
    }, indent=2) + "\n", encoding="utf-8")

    if triage["action"] == "monitor":
        return {"status": "skipped", "reason": "below autofix threshold", "triage": triage}

    env = load_env()
    fix_spec = generate_fix_spec(issue)

    if triage["action"] == "autofix" and env["github_repo"]:
        codex_result = dispatch_codex(fix_spec, env["github_repo"], env)
        if not codex_result["success"]:
            codex_result = dispatch_ralph(fix_spec, env["github_repo"], env)

        notify_telegram(
            f"Sentry autofix: {issue.get('title', 'unknown')}\n"
            f"Triage: score={triage['score']} action={triage['action']}\n"
            f"Fix: {codex_result['tool']} {'succeeded' if codex_result['success'] else 'failed'}"
        )

        return {"status": "dispatched", "triage": triage, "fix_result": codex_result}

    notify_telegram(
        f"Sentry review needed: {issue.get('title', 'unknown')}\n"
        f"Triage: score={triage['score']} ({', '.join(triage['reasons'])})"
    )

    return {"status": "review-flagged", "triage": triage, "fix_spec": fix_spec}


def main():
    parser = argparse.ArgumentParser(description="Sentry → Codex → PR autofix pipeline")
    sub = parser.add_subparsers(dest="command")

    triage_cmd = sub.add_parser("triage", help="Triage a Sentry issue by ID")
    triage_cmd.add_argument("--issue-id", required=True, help="Sentry issue ID")

    webhook_cmd = sub.add_parser("webhook", help="Process a Sentry webhook payload from stdin")

    fix_cmd = sub.add_parser("fix", help="Generate fix spec and dispatch for an issue ID")
    fix_cmd.add_argument("--issue-id", required=True, help="Sentry issue ID")
    fix_cmd.add_argument("--dry-run", action="store_true", help="Print fix spec without dispatching")

    args = parser.parse_args()

    if args.command == "webhook":
        payload = json.load(sys.stdin)
        result = process_webhook(payload)
        print(json.dumps(result, indent=2))

    elif args.command == "triage":
        # Fetch issue from Sentry API
        env = load_env()
        if not all([env["sentry_token"], env["sentry_org"], env["sentry_project"]]):
            print("Error: SENTRY_AUTH_TOKEN, SENTRY_ORG, SENTRY_PROJECT required", file=sys.stderr)
            raise SystemExit(1)

        result = subprocess.run(
            ["curl", "-s",
             "-H", f"Authorization: Bearer {env['sentry_token']}",
             f"{os.getenv('SENTRY_API_BASE', 'https://sentry.io/api/0')}/issues/{args.issue_id}/"],
            capture_output=True, text=True, check=False,
        )
        issue = json.loads(result.stdout)
        triage = triage_issue(issue)
        print(json.dumps(triage, indent=2))

    elif args.command == "fix":
        env = load_env()
        if not all([env["sentry_token"], env["sentry_org"], env["sentry_project"]]):
            print("Error: SENTRY_AUTH_TOKEN, SENTRY_ORG, SENTRY_PROJECT required", file=sys.stderr)
            raise SystemExit(1)

        api_base = os.getenv("SENTRY_API_BASE", "https://sentry.io/api/0")
        headers = ["-H", f"Authorization: Bearer {env['sentry_token']}"]

        # Fetch issue
        issue_result = subprocess.run(
            ["curl", "-s"] + headers + [f"{api_base}/issues/{args.issue_id}/"],
            capture_output=True, text=True, check=False,
        )
        issue = json.loads(issue_result.stdout)

        # Fetch latest event
        event_result = subprocess.run(
            ["curl", "-s"] + headers + [f"{api_base}/issues/{args.issue_id}/events/latest/"],
            capture_output=True, text=True, check=False,
        )
        latest_event = json.loads(event_result.stdout) if event_result.returncode == 0 else None

        fix_spec = generate_fix_spec(issue, latest_event)

        if args.dry_run:
            print(fix_spec)
        else:
            triage = triage_issue(issue)
            print(f"Triage: score={triage['score']} action={triage['action']}")
            print(f"Reasons: {', '.join(triage['reasons'])}")
            print()

            if not env["github_repo"]:
                print("Warning: SENTRY_GITHUB_REPO not set — printing fix spec only")
                print(fix_spec)
            else:
                print(f"Dispatching to Codex for {env['github_repo']}...")
                result = dispatch_codex(fix_spec, env["github_repo"], env)
                print(json.dumps(result, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
