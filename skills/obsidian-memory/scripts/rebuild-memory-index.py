#!/usr/bin/env python3
"""Build and query Rick's vault memory index."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


VAULT_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
INDEX_FILE = Path(
    os.path.expanduser(os.getenv("RICK_MEMORY_INDEX_FILE", str(VAULT_ROOT / "control" / "memory-index.json")))
)
ACCESS_LOG_FILE = Path(
    os.path.expanduser(
        os.getenv("RICK_MEMORY_ACCESS_LOG_FILE", str(VAULT_ROOT / "operations" / "memory-access.jsonl"))
    )
)
DASHBOARD_FILE = Path(
    os.path.expanduser(os.getenv("RICK_MEMORY_OVERVIEW_FILE", str(VAULT_ROOT / "dashboards" / "memory-overview.md")))
)
EXCLUDED_PREFIXES = (".obsidian/", "runtime/", "operations/")


def now() -> datetime:
    return datetime.now()


def iso_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def load_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    match = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not match:
        return {}, text
    frontmatter: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        frontmatter[key.strip()] = raw_value.strip().strip('"')
    return frontmatter, text[match.end() :]


def extract_preview(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:240]
    return ""


def infer_type(relative_path: str, frontmatter: dict[str, str]) -> str:
    if frontmatter.get("type"):
        return frontmatter["type"]
    if relative_path.endswith("summary.md"):
        return "project-summary"
    if relative_path.endswith("items.json"):
        return "fact-index"
    head = relative_path.split("/", 1)[0]
    return {
        "memory": "daily",
        "revenue": "revenue-snapshot",
        "decisions": "decision",
        "weekly-reviews": "weekly-review",
        "reflections": "reflection",
        "control": "control",
        "content": "content",
        "mailbox": "mailbox",
        "dashboards": "dashboard",
        "scorecards": "scorecard",
    }.get(head, "note")


def infer_project(relative_path: str) -> str:
    parts = relative_path.split("/")
    if len(parts) >= 3 and parts[0] == "projects":
        return parts[1]
    return ""


def load_access_map() -> dict[str, str]:
    if not ACCESS_LOG_FILE.exists():
        return {}
    last_accessed: dict[str, str] = {}
    for line in ACCESS_LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        path = str(payload.get("path", "")).strip()
        timestamp = str(payload.get("timestamp", "")).strip()
        if path and timestamp:
            last_accessed[path] = timestamp
    return last_accessed


def classify_tier(reference_time: datetime | None, current_time: datetime) -> str:
    if reference_time is None:
        return "cold"
    age = current_time - reference_time
    if age <= timedelta(days=7):
        return "hot"
    if age <= timedelta(days=30):
        return "warm"
    return "cold"


def iter_vault_files(vault_root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in vault_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(vault_root).as_posix()
        if any(relative.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
            continue
        if path.suffix == ".md" or path.name == "items.json":
            paths.append(path)
    return sorted(paths)


def build_entry(path: Path, access_map: dict[str, str], current_time: datetime) -> dict[str, Any]:
    relative = path.relative_to(VAULT_ROOT).as_posix()
    modified_at = datetime.fromtimestamp(path.stat().st_mtime)
    created_at = datetime.fromtimestamp(path.stat().st_ctime)
    last_accessed_at = parse_timestamp(access_map.get(relative))

    content = path.read_text(encoding="utf-8") if path.suffix == ".md" else path.read_text(encoding="utf-8")
    frontmatter, body = load_frontmatter(content) if path.suffix == ".md" else ({}, content)
    title = frontmatter.get("title") or next(
        (line[2:].strip() for line in body.splitlines() if line.startswith("# ")),
        path.stem,
    )
    links = re.findall(r"\[\[([^\]]+)\]\]", body) if path.suffix == ".md" else []
    tags = [tag.strip() for tag in frontmatter.get("tags", "").strip("[]").split(",") if tag.strip()]

    reference_time = max([stamp for stamp in (modified_at, last_accessed_at) if stamp is not None], default=None)
    return {
        "path": relative,
        "title": title,
        "type": infer_type(relative, frontmatter),
        "project": infer_project(relative),
        "tier": classify_tier(reference_time, current_time),
        "modified_at": iso_timestamp(modified_at),
        "created_at": iso_timestamp(created_at),
        "last_accessed_at": iso_timestamp(last_accessed_at) if last_accessed_at else "",
        "preview": extract_preview(body),
        "tags": tags,
        "wikilinks": links,
    }


def build_index() -> dict[str, Any]:
    current_time = now()
    access_map = load_access_map()
    entries = [build_entry(path, access_map, current_time) for path in iter_vault_files(VAULT_ROOT)]
    tier_counts = Counter(entry["tier"] for entry in entries)
    type_counts = Counter(entry["type"] for entry in entries)
    project_counts = Counter(entry["project"] for entry in entries if entry["project"])
    hot_entries = sorted(
        [entry for entry in entries if entry["tier"] == "hot"],
        key=lambda item: (item["last_accessed_at"] or item["modified_at"], item["path"]),
        reverse=True,
    )[:15]
    return {
        "generated_at": iso_timestamp(current_time),
        "root": str(VAULT_ROOT),
        "counts": {
            "entries": len(entries),
            "tiers": dict(tier_counts),
            "types": dict(type_counts),
            "projects": dict(project_counts),
        },
        "entries": entries,
        "hot_entries": hot_entries,
    }


def write_index(index: dict[str, Any]) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_dashboard(index: dict[str, Any]) -> str:
    lines = [
        "# Memory Overview",
        "",
        f"- Generated: {index['generated_at']}",
        f"- Total indexed entries: {index['counts']['entries']}",
        f"- Hot / warm / cold: {index['counts']['tiers'].get('hot', 0)} / {index['counts']['tiers'].get('warm', 0)} / {index['counts']['tiers'].get('cold', 0)}",
        "",
        "## Top Types",
        "",
        "| Type | Count |",
        "|------|-------|",
    ]
    for note_type, count in sorted(index["counts"]["types"].items()):
        lines.append(f"| {note_type} | {count} |")

    lines.extend(["", "## Hottest Entries", ""])
    for entry in index["hot_entries"]:
        lines.append(
            f"- `{entry['tier']}` [{entry['type']}] {entry['title']} — {entry['path']}"
        )

    if index["counts"]["projects"]:
        lines.extend(["", "## Project Coverage", "", "| Project | Indexed Entries |", "|---------|-----------------|"])
        for project, count in sorted(index["counts"]["projects"].items()):
            lines.append(f"| {project} | {count} |")

    return "\n".join(lines) + "\n"


def write_dashboard(index: dict[str, Any]) -> None:
    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_FILE.write_text(render_dashboard(index), encoding="utf-8")


def append_access_entries(entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    ACCESS_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = iso_timestamp(now())
    with ACCESS_LOG_FILE.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps({"timestamp": timestamp, "path": entry["path"]}, sort_keys=True) + "\n")


def load_or_build_index() -> dict[str, Any]:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return build_index()


def matches_search(entry: dict[str, Any], query: str) -> bool:
    haystack = " ".join(
        [
            entry["path"],
            entry["title"],
            entry["type"],
            entry["project"],
            entry["preview"],
            " ".join(entry.get("tags", [])),
            " ".join(entry.get("wikilinks", [])),
        ]
    ).lower()
    return query.lower() in haystack


def query_entries(
    entries: list[dict[str, Any]],
    *,
    note_type: str = "",
    search: str = "",
    after: str = "",
    recent: int = 0,
    tier: str = "",
    project: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    filtered = list(entries)
    if note_type:
        filtered = [entry for entry in filtered if entry["type"] == note_type]
    if search:
        filtered = [entry for entry in filtered if matches_search(entry, search)]
    if tier:
        filtered = [entry for entry in filtered if entry["tier"] == tier]
    if project:
        filtered = [entry for entry in filtered if entry["project"] == project]
    if after:
        after_dt = parse_timestamp(f"{after}T00:00:00")
        if after_dt:
            filtered = [
                entry
                for entry in filtered
                if parse_timestamp(entry.get("modified_at")) and parse_timestamp(entry["modified_at"]) >= after_dt
            ]
    sort_key = lambda item: (item.get("modified_at", ""), item["path"])
    filtered = sorted(filtered, key=sort_key, reverse=True)
    if recent > 0:
        return filtered[:recent]
    return filtered[:limit]


def print_human_results(results: list[dict[str, Any]]) -> None:
    if not results:
        print("No matching entries.")
        return
    for entry in results:
        modified = entry.get("modified_at", "")[:10]
        details = f"[{entry['tier']}] [{entry['type']}]"
        if entry.get("project"):
            details += f" [{entry['project']}]"
        print(f"{modified} {details} {entry['path']} -- {entry['title']}")
        if entry.get("preview"):
            print(f"    {entry['preview']}")


def rotate_access_log(max_age_days: int = 30) -> None:
    """Prune access log entries older than max_age_days."""
    if not ACCESS_LOG_FILE.exists():
        return
    cutoff = datetime.now() - timedelta(days=max_age_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    kept: list[str] = []
    try:
        for line in ACCESS_LOG_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("timestamp", "")
                if isinstance(ts, str) and ts >= cutoff_str:
                    kept.append(line)
            except json.JSONDecodeError:
                kept.append(line)  # keep unparseable lines
        ACCESS_LOG_FILE.write_text("\n".join(kept) + "\n" if kept else "", encoding="utf-8")
    except OSError:
        pass


def command_rebuild(args: argparse.Namespace) -> int:
    index = build_index()
    if args.write:
        write_index(index)
        write_dashboard(index)
    if not args.quiet:
        print(json.dumps(index["counts"], indent=2, sort_keys=True))
    rotate_access_log()
    return 0


def command_query(args: argparse.Namespace) -> int:
    index = load_or_build_index()
    results = query_entries(
        index["entries"],
        note_type=args.note_type,
        search=args.search,
        after=args.after,
        recent=args.recent,
        tier=args.tier,
        project=args.project,
        limit=args.limit,
    )
    append_access_entries(results)
    if args.json:
        print(json.dumps({"results": results}, indent=2))
    else:
        print_human_results(results)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rick memory index builder")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rebuild_parser = subparsers.add_parser("rebuild", help="Rebuild the memory index")
    rebuild_parser.add_argument("--write", action="store_true", help="Write index and dashboard to disk")
    rebuild_parser.add_argument("--quiet", action="store_true", help="Suppress stdout summary")
    rebuild_parser.set_defaults(func=command_rebuild)

    query_parser = subparsers.add_parser("query", help="Query the memory index")
    query_parser.add_argument("--type", dest="note_type", default="")
    query_parser.add_argument("--search", default="")
    query_parser.add_argument("--after", default="")
    query_parser.add_argument("--recent", type=int, default=0)
    query_parser.add_argument("--tier", choices=["hot", "warm", "cold"], default="")
    query_parser.add_argument("--project", default="")
    query_parser.add_argument("--limit", type=int, default=20)
    query_parser.add_argument("--json", action="store_true")
    query_parser.set_defaults(func=command_query)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
