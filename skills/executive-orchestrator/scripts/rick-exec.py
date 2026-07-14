#!/usr/bin/env python3
"""Rick executive loops: heartbeat, nightly review, weekly synthesis, and scorecards."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
CONTROL_DIR = DATA_ROOT / "control"
BRIEFINGS_DIR = CONTROL_DIR / "briefings"
MORNING_BRIEFS_DIR = CONTROL_DIR / "morning-briefs"
MEMORY_DIR = DATA_ROOT / "memory"
REVENUE_DIR = DATA_ROOT / "revenue"
WEEKLY_DIR = DATA_ROOT / "weekly-reviews"
OPS_HEALTH_FILE = CONTROL_DIR / "ops-health.md"
APPROVALS_FILE = CONTROL_DIR / "approvals.md"
DEPENDENCY_GAPS_FILE = CONTROL_DIR / "dependency-gaps.md"
TEMPLATE_DAILY = ROOT_DIR / "templates" / "daily-note.md"
SCORECARD_FILE = Path(
    os.path.expanduser(
        os.getenv("RICK_PORTFOLIO_SCORECARDS_FILE", str(DATA_ROOT / "scorecards" / "portfolio.json"))
    )
)
EXECUTION_LEDGER_FILE = Path(
    os.path.expanduser(
        os.getenv("RICK_EXECUTION_LEDGER_FILE", str(DATA_ROOT / "operations" / "execution-ledger.jsonl"))
    )
)
TOKEN_USAGE_FILE = Path(
    os.path.expanduser(os.getenv("RICK_LLM_USAGE_LOG_FILE", str(DATA_ROOT / "operations" / "llm-usage.jsonl")))
)
TOKEN_BUDGET_FILE = Path(
    os.path.expanduser(os.getenv("RICK_TOKEN_BUDGET_FILE", str(ROOT_DIR / "config" / "token-budgets.json")))
)
RUNTIME_DB_FILE = Path(
    os.path.expanduser(os.getenv("RICK_RUNTIME_DB_FILE", str(DATA_ROOT / "runtime" / "rick-runtime.db")))
)


DEFAULT_PORTFOLIO = {
    "projects": [
        {
            "slug": "partner-connector",
            "name": "Partner Connector",
            "status": "active",
            "revenue_now": 9,
            "growth_potential": 7,
            "speed_to_cash": 8,
            "distribution_fit": 6,
            "confidence": 8,
            "maintenance_load": 6,
            "strategic_fit": 8,
            "notes": "Protect current revenue and improve activation.",
        },
        {
            "slug": "personal-brand",
            "name": "Personal Brand",
            "status": "active",
            "revenue_now": 4,
            "growth_potential": 9,
            "speed_to_cash": 6,
            "distribution_fit": 9,
            "confidence": 7,
            "maintenance_load": 4,
            "strategic_fit": 9,
            "notes": "Primary audience and distribution engine.",
        },
        {
            "slug": "info-products",
            "name": "Info Products",
            "status": "active",
            "revenue_now": 3,
            "growth_potential": 9,
            "speed_to_cash": 8,
            "distribution_fit": 8,
            "confidence": 7,
            "maintenance_load": 5,
            "strategic_fit": 9,
            "notes": "Best path to higher-margin launches.",
        },
        {
            "slug": "404-agency",
            "name": "404 Agency",
            "status": "explore",
            "revenue_now": 2,
            "growth_potential": 5,
            "speed_to_cash": 5,
            "distribution_fit": 4,
            "confidence": 4,
            "maintenance_load": 7,
            "strategic_fit": 4,
            "notes": "Keep contained unless it proves demand.",
        },
        {
            "slug": "lingualive",
            "name": "LinguaLive",
            "status": "nurture",
            "revenue_now": 2,
            "growth_potential": 6,
            "speed_to_cash": 4,
            "distribution_fit": 5,
            "confidence": 5,
            "maintenance_load": 6,
            "strategic_fit": 5,
            "notes": "Maintain optionality without distracting from core monetization.",
        },
    ]
}

STATUS_MULTIPLIER = {
    "active": 1.0,
    "explore": 0.9,
    "nurture": 0.85,
    "paused": 0.6,
}

ROUTING = {
    "strategy": ("RICK_MODEL_OPENAI_STRATEGIC", "gpt-5.6-sol"),
    "coding": ("RICK_MODEL_OPENAI_CODING", "gpt-5.6-sol"),
    "writing": ("RICK_MODEL_ANTHROPIC_WORKHORSE", "claude-sonnet-4-6"),
    "review": ("RICK_MODEL_ANTHROPIC_STRATEGIC", "claude-opus-4-8"),
    "heartbeat": ("RICK_MODEL_GOOGLE_BUDGET", "gemini-3.1-flash-lite-preview"),
    "analysis": ("RICK_MODEL_GOOGLE_WORKHORSE", "gemini-3.1-pro-preview"),
    "research": ("RICK_MODEL_XAI_RESEARCH", "grok-4-latest"),
}


def ensure_workspace(write: bool) -> None:
    if not write:
        return
    for path in (BRIEFINGS_DIR, MORNING_BRIEFS_DIR, MEMORY_DIR, REVENUE_DIR, WEEKLY_DIR, SCORECARD_FILE.parent):
        path.mkdir(parents=True, exist_ok=True)


def note_path(day: datetime | None = None) -> Path:
    stamp = (day or datetime.now()).strftime("%Y-%m-%d")
    return MEMORY_DIR / f"{stamp}.md"


def ensure_daily_note(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if TEMPLATE_DAILY.exists():
        path.write_text(TEMPLATE_DAILY.read_text(encoding="utf-8").replace("{{date}}", path.stem), encoding="utf-8")
    else:
        path.write_text(f"# {path.stem}\n\n## Today's Plan\n\n", encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def load_jsonl(path: Path, days: int | None = None) -> list[dict]:
    if not path.exists():
        return []

    cutoff = datetime.now() - timedelta(days=days) if days is not None else None
    rows: list[dict] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        stamp = parse_timestamp(payload.get("timestamp"))
        if cutoff is not None and (stamp is None or stamp < cutoff):
            continue
        rows.append(payload)
    return rows


def conversion_snapshot() -> list[str]:
    script = DATA_ROOT / "scripts" / "fetch-ga-conversions.py"
    if not script.exists():
        return ["- Conversion script missing."]
    try:
        result = subprocess.run(
            ["python3", str(script)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        payload = json.loads(result.stdout)
    except Exception:
        return ["- Conversion data unavailable."]

    lines = [f"- Source: {payload.get('source', 'unknown')}"]
    if payload.get("measurement_id"):
        lines.append(f"- Measurement ID: {payload['measurement_id']}")
    events = payload.get("events") or {}
    if events:
        lines.append(
            "- Key events (7d): "
            f"stripe_click={events.get('stripe_click', 0)}, "
            f"conversion_click={events.get('conversion_click', 0)}, "
            f"newsletter_signup={events.get('newsletter_signup', 0)}, "
            f"roast_submit={events.get('roast_submit', 0)}, "
            f"roast_complete={events.get('roast_complete', 0)}"
        )
    if payload.get("roast_calls_7d") is not None:
        lines.append(f"- Roast calls from Railway/log fallback: {payload.get('roast_calls_7d', 0)}")
    manual_events = payload.get("manual_events") or {}
    if manual_events:
        summary = ", ".join(f"{key}={value}" for key, value in sorted(manual_events.items()))
        lines.append(f"- Manual conversion log: {summary}")
    return lines


def append_section(path: Path, heading: str, body: str) -> None:
    ensure_daily_note(path)
    existing = read_text(path).rstrip()
    with path.open("w", encoding="utf-8") as handle:
        if existing:
            handle.write(existing + "\n\n")
        handle.write(f"{heading}\n\n{body}\n")


def replace_plan_section(path: Path, tasks: list[str]) -> None:
    ensure_daily_note(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    start = None
    end = None

    for idx, line in enumerate(lines):
        if line.startswith("## Today's Plan"):
            start = idx
            continue
        if start is not None and idx > start and line.startswith("## "):
            end = idx
            break

    plan_lines = ["## Today's Plan", ""]
    for task in tasks:
        plan_lines.append(f"- [ ] {task}")
    if not tasks:
        plan_lines.append("- [ ] Review portfolio ranking")

    if start is None:
        new_lines = lines + [""] + plan_lines
    else:
        if end is None:
            end = len(lines)
        new_lines = lines[:start] + plan_lines + lines[end:]

    path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def load_scorecards() -> list[dict]:
    if SCORECARD_FILE.exists():
        try:
            payload = json.loads(SCORECARD_FILE.read_text(encoding="utf-8"))
            projects = payload.get("projects", [])
            if isinstance(projects, list) and projects:
                return projects
        except json.JSONDecodeError:
            pass
    return DEFAULT_PORTFOLIO["projects"]


def score_project(project: dict) -> float:
    weighted = (
        project.get("revenue_now", 0) * 0.26
        + project.get("growth_potential", 0) * 0.18
        + project.get("speed_to_cash", 0) * 0.18
        + project.get("distribution_fit", 0) * 0.12
        + project.get("confidence", 0) * 0.12
        + project.get("strategic_fit", 0) * 0.14
        - project.get("maintenance_load", 0) * 0.10
    )
    return round(weighted * STATUS_MULTIPLIER.get(project.get("status", "active"), 1.0), 2)


def ranked_projects() -> list[dict]:
    ranked: list[dict] = []
    for project in load_scorecards():
        enriched = dict(project)
        enriched["score"] = score_project(project)
        ranked.append(enriched)
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def parse_checkbox_stats(path: Path) -> dict:
    text = read_text(path)
    open_tasks: list[str] = []
    done_tasks: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*-\s*\[( |x|X)\]\s+(.*)$", line)
        if not match:
            continue
        task = match.group(2).strip()
        if match.group(1).lower() == "x":
            done_tasks.append(task)
        else:
            open_tasks.append(task)
    return {
        "open": open_tasks,
        "done": done_tasks,
        "open_count": len(open_tasks),
        "done_count": len(done_tasks),
    }


def latest_file_mtime(root: Path) -> datetime | None:
    if not root.exists():
        return None
    latest = None
    for path in root.rglob("*"):
        if path.is_file():
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            if latest is None or modified > latest:
                latest = modified
    return latest


def shipped_assets_last_days(days: int) -> int:
    roots = [
        DATA_ROOT / "content" / "newsletters",
        DATA_ROOT / "content" / "social",
        DATA_ROOT / "projects",
    ]
    since = datetime.now() - timedelta(days=days)
    count = 0
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime) >= since:
                count += 1
    return count


def shipping_status() -> dict:
    latest = None
    for root in (DATA_ROOT / "content", DATA_ROOT / "projects"):
        modified = latest_file_mtime(root)
        if modified and (latest is None or modified > latest):
            latest = modified
    if latest is None:
        return {"latest": None, "hours_since": None, "stale": True}
    hours_since = round((datetime.now() - latest).total_seconds() / 3600, 1)
    return {"latest": latest, "hours_since": hours_since, "stale": hours_since >= 72}


def latest_revenue_snapshot() -> dict:
    candidates = sorted(p for p in REVENUE_DIR.glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem))
    if not candidates:
        return {"available": False}

    latest = candidates[-1]
    text = read_text(latest)
    net_match = re.search(r"\|\s*Period Net Revenue\s*\|\s*([^\|]+)\|", text)
    gap_match = re.search(r"\|\s*Gap\s*\|\s*([^\|]+)\|", text)
    period_match = re.search(r"\*\*Period:\*\*\s*(.+)", text)

    def parse_net(path: Path) -> float:
        match = re.search(r"\|\s*Period Net Revenue\s*\|\s*([^\|]+)\|", read_text(path))
        if not match:
            return 0.0
        try:
            return float(re.sub(r"[^\d.\-]", "", match.group(1)) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    velocity_path = REVENUE_DIR / "velocity.json"
    try:
        velocity = json.loads(read_text(velocity_path)) if velocity_path.exists() else {}
    except json.JSONDecodeError:
        velocity = {}

    return {
        "available": True,
        "path": str(latest),
        "date": latest.stem,
        "period": period_match.group(1).strip() if period_match else "unknown",
        "net": net_match.group(1).strip() if net_match else "unknown",
        "rev_7d": f"${sum(parse_net(path) for path in candidates[-7:]):.2f}",
        "mrr": f"${float(velocity.get('current_mrr', 0.0) or 0.0):.2f}",
        "mrr_delta_7d": f"${float(velocity.get('delta_7d', 0.0) or 0.0):+.2f}",
        "gap": gap_match.group(1).strip() if gap_match else None,
    }


def count_open_approvals() -> int:
    # Count from the runtime DB (source of truth), not the approvals.md mirror —
    # the mirror goes stale and under-reported open approvals (2026-07-14: briefing
    # said 0 while 3 were open). Matches proactive.py/engine.py counting.
    if not RUNTIME_DB_FILE.exists():
        return 0
    connection = sqlite3.connect(str(RUNTIME_DB_FILE))
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM approvals WHERE status = 'open'"
        ).fetchone()[0]
    finally:
        connection.close()


def regenerate_approvals_mirror() -> None:
    # approvals.md is a GENERATED mirror of the runtime DB approvals table.
    # Regenerated on every heartbeat --write because approvals are inserted by
    # multiple paths (engine ApprovalRequired, reply_router, ad-hoc sessions) and
    # per-path appends left the mirror stale/lying (2026-06-12 -> 2026-07-14).
    if not RUNTIME_DB_FILE.exists():
        return
    connection = sqlite3.connect(str(RUNTIME_DB_FILE))
    try:
        rows = connection.execute(
            "SELECT id, status, area, request_text, impact_text, requested_by, created_at "
            "FROM approvals ORDER BY (status = 'open') DESC, created_at DESC"
        ).fetchall()
    finally:
        connection.close()

    def cell(text: str) -> str:
        # Table cells must be single-line and pipe-free or downstream parsers
        # (vlad-action-board, build-daily-brief) misread the columns.
        return " ".join((text or "").split()).replace("|", "/")

    lines = [
        "# Approvals",
        "",
        "> GENERATED from the runtime DB (`approvals` table) by rick-exec.py "
        "heartbeat --write — do not hand-edit; changes are overwritten every "
        f"heartbeat. Last generated: {datetime.now():%Y-%m-%d %H:%M:%S}.",
        "",
        "| Date | Status | Owner | Area | Request | Impact |",
        "|------|--------|-------|------|---------|--------|",
    ]
    for row_id, status, area, request_text, impact_text, requested_by, created_at in rows:
        lines.append(
            f"| {created_at} | {status} | {requested_by} | {area} "
            f"| [{row_id}] {cell(request_text)} | {cell(impact_text)} |"
        )
    APPROVALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPROVALS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def dependency_gap_status() -> str:
    text = read_text(DEPENDENCY_GAPS_FILE)
    if not text:
        return "unknown"
    if "No missing dependencies detected." in text:
        return "clear"
    return "attention-needed"


def ops_health_summary() -> str:
    text = read_text(OPS_HEALTH_FILE)
    if not text:
        return "unknown"
    if "| fail |" in text:
        return "failing checks"
    if "| warn |" in text:
        return "warnings present"
    return "healthy"


def route_label(kind: str) -> str:
    if kind == "strategy" and os.getenv("RICK_STRATEGY_PANEL_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}:
        panel_models = os.getenv(
            "RICK_STRATEGY_PANEL_MODELS",
            "openai:gpt-5.6-sol,anthropic:claude-opus-4-8,google:gemini-3.1-pro-preview",
        )
        synthesis = os.getenv("RICK_STRATEGY_PANEL_SYNTHESIS_MODEL", "openai:gpt-5.6-sol")
        return f"{kind} -> panel[{panel_models}] -> synth[{synthesis}]"
    env_name, default_model = ROUTING[kind]
    return f"{kind} -> {os.getenv(env_name, default_model)}"


def token_budget_caps() -> dict[str, float]:
    if not TOKEN_BUDGET_FILE.exists():
        return {}
    try:
        payload = json.loads(TOKEN_BUDGET_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    caps = payload.get("daily_usd_caps", {})
    if not isinstance(caps, dict):
        return {}
    normalized: dict[str, float] = {}
    for key, value in caps.items():
        try:
            normalized[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return normalized


def token_economics_lines(days: int = 7) -> list[str]:
    events = load_jsonl(TOKEN_USAGE_FILE, days=days)
    if not events:
        return ["- No LLM usage logged yet."]

    today = datetime.now().date()
    today_spend = 0.0
    by_bucket: dict[str, float] = {}
    today_by_bucket: dict[str, float] = {}
    for event in events:
        usd = float(event.get("usd", 0) or 0)
        bucket = str(event.get("bucket", "unknown"))
        by_bucket[bucket] = by_bucket.get(bucket, 0.0) + usd
        stamp = parse_timestamp(event.get("timestamp"))
        if stamp and stamp.date() == today:
            today_spend += usd
            today_by_bucket[bucket] = today_by_bucket.get(bucket, 0.0) + usd

    lines = [
        f"- Today spend: ${today_spend:.2f}",
        f"- Last {days}d spend: ${sum(float(event.get('usd', 0) or 0) for event in events):.2f}",
        f"- Logged usage events: {len(events)}",
    ]

    caps = token_budget_caps()
    for bucket, spend in sorted(by_bucket.items(), key=lambda item: item[1], reverse=True)[:3]:
        cap = caps.get(bucket)
        today_bucket_spend = today_by_bucket.get(bucket, 0.0)
        if cap is None:
            lines.append(f"- {bucket}: ${today_bucket_spend:.2f} today, ${spend:.2f} in {days}d (no cap configured)")
        else:
            status = "over cap" if today_bucket_spend > cap else "within cap"
            lines.append(f"- {bucket}: ${today_bucket_spend:.2f} today / ${cap:.2f} ({status})")
    return lines


def execution_activity_lines(days: int = 7) -> list[str]:
    events = load_jsonl(EXECUTION_LEDGER_FILE, days=days)
    if not events:
        return ["- No execution ledger events yet."]

    lines = [f"- Logged events in last {days}d: {len(events)}"]
    latest = events[-1]
    lines.append(
        f"- Latest event: {latest.get('kind', 'unknown')} / {latest.get('status', 'unknown')} / {latest.get('title', '')}"
    )

    counts: dict[str, int] = {}
    for event in events:
        kind = str(event.get("kind", "unknown"))
        counts[kind] = counts.get(kind, 0) + 1
    mix = ", ".join(f"{kind}: {count}" for kind, count in sorted(counts.items()))
    lines.append(f"- Mix: {mix}")
    return lines


def build_actions(ranked: list[dict], task_stats: dict, shipping: dict, revenue: dict) -> list[dict]:
    actions: list[dict] = []

    if shipping["stale"]:
        actions.append(
            {
                "title": "Ship one external asset in the next 24 hours",
                "reason": "No externally visible output landed in the last 72 hours.",
                "route": route_label("writing"),
            }
        )

    if not revenue["available"]:
        actions.append(
            {
                "title": "Run the revenue dashboard and log a fresh snapshot",
                "reason": "Rick cannot prioritize well without current revenue data.",
                "route": route_label("analysis"),
            }
        )

    if task_stats["open_count"] > 7:
        actions.append(
            {
                "title": "Collapse today's task list to three real priorities",
                "reason": f"Today's note has {task_stats['open_count']} open tasks, which dilutes execution.",
                "route": route_label("heartbeat"),
            }
        )

    for project in ranked[:3]:
        slug = project["slug"]
        if slug == "partner-connector":
            actions.append(
                {
                    "title": "Audit revenue leaks and activation friction in Partner Connector",
                    "reason": "Highest near-term revenue surface; protect current cash first.",
                    "route": route_label("strategy"),
                }
            )
        elif slug == "personal-brand":
            actions.append(
                {
                    "title": "Publish one brand asset that grows audience capture",
                    "reason": "Distribution is under-owned unless Rick ships content continuously.",
                    "route": route_label("writing"),
                }
            )
        elif slug == "info-products":
            actions.append(
                {
                    "title": "Select the next info-product candidate and create a launch brief",
                    "reason": "Fastest high-margin path toward the revenue target.",
                    "route": route_label("strategy"),
                }
            )
        elif slug == "lingualive":
            actions.append(
                {
                    "title": "Define the smallest monetizable LinguaLive experiment",
                    "reason": "Keep optionality alive without letting it become a distraction.",
                    "route": route_label("analysis"),
                }
            )

    unique: list[dict] = []
    seen: set[str] = set()
    for action in actions:
        if action["title"] in seen:
            continue
        seen.add(action["title"])
        unique.append(action)
    return unique[:5]


def render_score_table(ranked: list[dict]) -> str:
    lines = [
        "| Rank | Project | Score | Status | Notes |",
        "|------|---------|-------|--------|-------|",
    ]
    for index, project in enumerate(ranked, start=1):
        lines.append(
            f"| {index} | {project['name']} | {project['score']:.2f} | {project.get('status', 'active')} | {project.get('notes', '')} |"
        )
    return "\n".join(lines)


def heartbeat_body(now: datetime) -> str:
    ranked = ranked_projects()
    note = note_path(now)
    task_stats = parse_checkbox_stats(note)
    shipping = shipping_status()
    revenue = latest_revenue_snapshot()
    actions = build_actions(ranked, task_stats, shipping, revenue)

    lines = [
        f"# Rick Heartbeat - {now.strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Execution",
        f"- Open tasks in today's note: {task_stats['open_count']}",
        f"- Completed tasks in today's note: {task_stats['done_count']}",
        f"- Today's note: {note}",
        "",
        "## Revenue",
    ]
    if revenue["available"]:
        lines.extend(
            [
                f"- Latest snapshot: {revenue['date']} ({revenue['period']})",
                f"- MRR: {revenue.get('mrr', 'unknown')}",
                f"- Revenue last 7d: {revenue.get('rev_7d', 'unknown')}",
                f"- MRR delta 7d: {revenue.get('mrr_delta_7d', 'unknown')}",
                f"- Period net revenue: {revenue['net']}",
                f"- Gap: {revenue['gap'] or 'n/a'}",
            ]
        )
    else:
        lines.append("- No revenue snapshot found yet.")

    lines.extend(
        [
            "",
            "## Control Plane",
            f"- Ops health: {ops_health_summary()}",
            f"- Open approvals: {count_open_approvals()}",
            f"- Dependency gaps: {dependency_gap_status()}",
            "",
            "## Token Economics",
        ]
    )
    lines.extend(token_economics_lines())

    lines.extend(
        [
            "",
            "## Operations Ledger",
        ]
    )
    lines.extend(execution_activity_lines())

    lines.extend(
        [
            "",
            "## Shipping",
            f"- Hours since last shipped artifact: {shipping['hours_since'] if shipping['hours_since'] is not None else 'unknown'}",
            f"- Shipping stale: {'yes' if shipping['stale'] else 'no'}",
            "",
            "## Portfolio Ranking",
            render_score_table(ranked[:5]),
            "",
            "## Next Actions",
        ]
    )
    for action in actions:
        lines.append(f"- {action['title']} [{action['route']}]")
        lines.append(f"  Reason: {action['reason']}")
    return "\n".join(lines)


def nightly_body(now: datetime) -> tuple[str, list[str]]:
    ranked = ranked_projects()
    note = note_path(now)
    task_stats = parse_checkbox_stats(note)
    shipping = shipping_status()
    revenue = latest_revenue_snapshot()
    actions = build_actions(ranked, task_stats, shipping, revenue)
    tomorrow_tasks = [action["title"] for action in actions[:3]]

    risk = "No fresh revenue snapshot available."
    if dependency_gap_status() != "clear":
        risk = "Open dependency gaps are blocking clean execution."
    elif shipping["stale"]:
        risk = "No externally visible shipping in the last 72 hours."
    elif ranked:
        risk = f"Top portfolio pressure point: {ranked[0]['name']}."

    lines = [
        f"# Rick Nightly Review - {now.strftime('%Y-%m-%d')}",
        "",
        "## Scoreboard",
        f"- Completed today: {task_stats['done_count']}",
        f"- Still open: {task_stats['open_count']}",
        f"- Shipped artifacts in last 7 days: {shipped_assets_last_days(7)}",
        f"- Open approvals: {count_open_approvals()}",
        f"- Ops health: {ops_health_summary()}",
        f"- Logged execution events (7d): {len(load_jsonl(EXECUTION_LEDGER_FILE, days=7))}",
        "",
        "## Revenue Context",
    ]
    if revenue["available"]:
        lines.extend(
            [
                f"- Latest snapshot: {revenue['date']} ({revenue['period']})",
                f"- MRR: {revenue.get('mrr', 'unknown')}",
                f"- Revenue last 7d: {revenue.get('rev_7d', 'unknown')}",
                f"- MRR delta 7d: {revenue.get('mrr_delta_7d', 'unknown')}",
                f"- Period net revenue: {revenue['net']}",
                f"- Gap: {revenue['gap'] or 'n/a'}",
            ]
        )
    else:
        lines.append("- No revenue snapshot found yet.")

    lines.extend(
        [
            "",
            "## Conversion Signals",
        ]
    )
    lines.extend(conversion_snapshot())

    lines.extend(
        [
            "",
            "## Token Economics",
        ]
    )
    lines.extend(token_economics_lines())

    lines.extend(
        [
            "",
            "## Biggest Risk",
            f"- {risk}",
            "",
            "## Tomorrow's Top 3",
        ]
    )
    for task in tomorrow_tasks:
        lines.append(f"- {task}")

    lines.extend(
        [
            "",
            "## Portfolio Ranking",
            render_score_table(ranked[:5]),
        ]
    )

    return "\n".join(lines), tomorrow_tasks


def weekly_body(now: datetime) -> str:
    ranked = ranked_projects()
    shipping = shipping_status()
    revenue = latest_revenue_snapshot()
    shipped = shipped_assets_last_days(7)

    lines = [
        f"# Rick Weekly Review - {now.strftime('%Y-W%W')}",
        "",
        "## Output",
        f"- Shipped artifacts in last 7 days: {shipped}",
        f"- Shipping stale right now: {'yes' if shipping['stale'] else 'no'}",
        f"- Open approvals: {count_open_approvals()}",
        f"- Dependency gaps: {dependency_gap_status()}",
        f"- Logged execution events (7d): {len(load_jsonl(EXECUTION_LEDGER_FILE, days=7))}",
        "",
        "## Revenue Context",
    ]
    if revenue["available"]:
        lines.extend(
            [
                f"- Latest snapshot: {revenue['date']} ({revenue['period']})",
                f"- MRR: {revenue.get('mrr', 'unknown')}",
                f"- Revenue last 7d: {revenue.get('rev_7d', 'unknown')}",
                f"- MRR delta 7d: {revenue.get('mrr_delta_7d', 'unknown')}",
                f"- Period net revenue: {revenue['net']}",
                f"- Gap: {revenue['gap'] or 'n/a'}",
            ]
        )
    else:
        lines.append("- No revenue snapshot found yet.")

    lines.extend(
        [
            "",
            "## Token Economics",
        ]
    )
    lines.extend(token_economics_lines())

    lines.extend(
        [
            "",
            "## Portfolio Ranking",
            render_score_table(ranked),
            "",
            "## Focus For Next Week",
            "- Protect the highest-scoring revenue surface first.",
            "- Ship at least one external audience asset and one monetization asset.",
            "- Remove or pause any project that consumes energy without improving score.",
        ]
    )
    return "\n".join(lines)


def write_brief(name: str, body: str) -> Path:
    path = BRIEFINGS_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n", encoding="utf-8")
    return path


def run_heartbeat(write: bool) -> None:
    now = datetime.now()
    body = heartbeat_body(now)
    print(body)
    if write:
        regenerate_approvals_mirror()
        filename = f"{now.strftime('%Y-%m-%d')}-heartbeat-{now.strftime('%H%M')}.md"
        path = write_brief(filename, body)
        append_section(note_path(now), f"## Heartbeat {now.strftime('%H:%M')}", body.split("\n", 2)[2])
        print(f"\nWritten to {path}")


def run_nightly(write: bool) -> None:
    now = datetime.now()
    body, tomorrow_tasks = nightly_body(now)
    print(body)
    if write:
        filename = f"{now.strftime('%Y-%m-%d')}-nightly.md"
        path = write_brief(filename, body)
        tomorrow = note_path(now + timedelta(days=1))
        replace_plan_section(tomorrow, tomorrow_tasks)
        print(f"\nWritten to {path}")
        print(f"Updated tomorrow note: {tomorrow}")


def run_weekly(write: bool) -> None:
    now = datetime.now()
    body = weekly_body(now)
    print(body)
    if write:
        weekly_name = f"{now.strftime('%Y')}-W{now.strftime('%W')}.md"
        path = WEEKLY_DIR / weekly_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "\n", encoding="utf-8")
        print(f"\nWritten to {path}")


def run_score(write: bool) -> None:
    ranked = ranked_projects()
    table = render_score_table(ranked)
    print(table)
    if write:
        path = DATA_ROOT / "dashboards" / "portfolio-ranking.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Portfolio Ranking\n\n" + table + "\n", encoding="utf-8")
        print(f"\nWritten to {path}")


STRATEGY_CHALLENGER_PROMPT_FILE = ROOT_DIR / "prompts" / "strategy-challenger.md"


def run_challenge(question: str, write: bool) -> None:
    """Spawn Opus with the strategy-challenger system prompt for adversarial review."""
    import urllib.request, urllib.error

    if not STRATEGY_CHALLENGER_PROMPT_FILE.exists():
        print(f"ERROR: strategy-challenger prompt not found at {STRATEGY_CHALLENGER_PROMPT_FILE}")
        return

    system_prompt = STRATEGY_CHALLENGER_PROMPT_FILE.read_text(encoding="utf-8")
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        return

    model = os.getenv("RICK_MODEL_ANTHROPIC_STRATEGIC", "claude-opus-4-8")
    payload = json.dumps({
        "model": model,
        "max_tokens": 2000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": question}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        output = data["content"][0]["text"]
    except urllib.error.HTTPError as e:
        output = f"ERROR HTTP {e.code}: {e.read().decode()}"
    except Exception as e:
        output = f"ERROR: {e}"

    now = datetime.now()
    header = f"# Strategy Challenge — {now.strftime('%Y-%m-%d %H:%M')}\n\n**Question:** {question}\n\n---\n\n"
    body = header + output
    print(body)

    if write:
        filename = f"{now.strftime('%Y-%m-%d')}-challenge-{now.strftime('%H%M')}.md"
        path = BRIEFINGS_DIR / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "\n", encoding="utf-8")
        print(f"\nWritten to {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Rick executive loops")
    parser.add_argument("mode", choices=["heartbeat", "nightly", "weekly", "score", "challenge"])
    parser.add_argument("--write", action="store_true", help="Write results into Rick memory files")
    parser.add_argument("--question", "-q", type=str, default="", help="Question for challenge mode")
    args = parser.parse_args()

    ensure_workspace(args.write)

    if args.mode == "heartbeat":
        run_heartbeat(args.write)
    elif args.mode == "nightly":
        run_nightly(args.write)
    elif args.mode == "weekly":
        run_weekly(args.write)
    elif args.mode == "challenge":
        if not args.question:
            print("ERROR: --question / -q required for challenge mode")
            return 1
        run_challenge(args.question, args.write)
    else:
        run_score(args.write)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
