#!/usr/bin/env python3
"""Shared Telegram topic/thread helpers for Rick."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
TOPIC_REPORT_FILE = DATA_ROOT / "control" / "telegram-topics.md"
THREAD_MODE_VALUES = {"off", "hybrid", "workflow"}

DEFAULT_TOPIC_DEFINITIONS: list[dict[str, str]] = [
    {
        "key": "ceo-hq",
        "title": "CEO HQ",
        "purpose": "ceo-hq",
        "lane": "ceo-lane",
        "emoji": "🧠",
        "seed_text": "Rick CEO HQ is live. Use this topic for high-level status, decisions, and founder control.",
    },
    {
        "key": "approvals",
        "title": "Approvals",
        "purpose": "approvals",
        "lane": "ceo-lane",
        "emoji": "✅",
        "seed_text": "Rick approvals live here. Review founder approvals and resume blocked workflows from this topic.",
    },
    {
        "key": "product-lab",
        "title": "Product Lab",
        "purpose": "product-lab",
        "lane": "product-lane",
        "emoji": "🚀",
        "seed_text": "Rick product work starts here. Queue new launches here to auto-create workflow topics.",
    },
    {
        "key": "distribution",
        "title": "Distribution",
        "purpose": "distribution",
        "lane": "distribution-lane",
        "emoji": "📣",
        "seed_text": "Rick distribution updates land here when no workflow topic is bound.",
    },
    {
        "key": "customer",
        "title": "Customer",
        "purpose": "customer",
        "lane": "customer-lane",
        "emoji": "🤝",
        "seed_text": "Rick customer fulfillment, lifecycle, and support work lives here when no workflow topic is bound.",
    },
    {
        "key": "ops-alerts",
        "title": "Ops Alerts",
        "purpose": "ops-alerts",
        "lane": "ops-lane",
        "emoji": "🛠",
        "seed_text": "Rick heartbeat, watchdog, and incident alerts land here unless a workflow topic is more specific.",
    },
]

PURPOSE_TOPIC_KEYS = {
    "approval": "approvals",
    "approvals": "approvals",
    "ceo": "ceo-hq",
    "ceo-hq": "ceo-hq",
    "distribution": "distribution",
    "customer": "customer",
    "ops": "ops-alerts",
    "ops-alerts": "ops-alerts",
    "product": "product-lab",
    "product-lab": "product-lab",
}

LANE_TOPIC_KEYS = {
    "ceo-lane": "ceo-hq",
    "product-lane": "product-lab",
    "distribution-lane": "distribution",
    "customer-lane": "customer",
    "ops-lane": "ops-alerts",
    "research-lane": "product-lab",
}


@dataclass(frozen=True)
class TelegramTarget:
    chat_id: str
    thread_id: int | None = None

    @property
    def encoded(self) -> str:
        return format_telegram_target(self.chat_id, self.thread_id)


def now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def telegram_thread_mode() -> str:
    mode = os.getenv("RICK_TELEGRAM_THREAD_MODE", "off").strip().lower()
    return mode if mode in THREAD_MODE_VALUES else "off"


def thread_mode_enabled() -> bool:
    return telegram_thread_mode() != "off"


def allowed_chat_id() -> str:
    return os.getenv("RICK_TELEGRAM_ALLOWED_CHAT_ID", "").strip()


def forum_chat_id() -> str:
    return os.getenv("RICK_TELEGRAM_FORUM_CHAT_ID", "").strip() or allowed_chat_id()


def authorized_chat_ids() -> set[str]:
    founder = os.getenv("RICK_TELEGRAM_FOUNDER_CHAT_ID", "").strip()
    return {value for value in {allowed_chat_id(), forum_chat_id(), founder} if value}


def openclaw_main_agent_id() -> str:
    value = os.getenv("RICK_OPENCLAW_MAIN_AGENT_ID", "rick").strip()
    return value or "rick"


def topics_config_path() -> Path:
    configured = os.getenv("RICK_TELEGRAM_TOPICS_FILE", "").strip()
    if configured:
        return Path(os.path.expanduser(configured))
    return ROOT_DIR / "config" / "telegram-topics.json"


def topics_example_path() -> Path:
    return ROOT_DIR / "config" / "telegram-topics.example.json"


def format_telegram_target(chat_id: str | int, thread_id: int | None = None) -> str:
    normalized_chat = str(chat_id).strip()
    if not normalized_chat:
        return ""
    if thread_id is None:
        return normalized_chat
    return f"{normalized_chat}:topic:{int(thread_id)}"


def parse_telegram_target(value: str | None) -> TelegramTarget | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if ":topic:" in normalized:
        chat_id, _, thread_value = normalized.partition(":topic:")
        try:
            return TelegramTarget(chat_id=str(chat_id).strip(), thread_id=int(thread_value))
        except (TypeError, ValueError):
            return TelegramTarget(chat_id=str(chat_id).strip(), thread_id=None)
    return TelegramTarget(chat_id=normalized, thread_id=None)


def format_openclaw_session_key(chat_id: str | int, thread_id: int | None, *, agent_id: str | None = None) -> str:
    normalized_chat = str(chat_id).strip()
    if not normalized_chat or thread_id is None:
        return ""
    resolved_agent = (agent_id or openclaw_main_agent_id()).strip() or "rick"
    return f"agent:{resolved_agent}:telegram:group:{normalized_chat}:topic:{int(thread_id)}"


def load_topic_definitions() -> list[dict[str, str]]:
    path = topics_config_path()
    raw_topics: Any = None
    if path.exists():
        try:
            raw_topics = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw_topics = None
    elif topics_example_path().exists():
        try:
            raw_topics = json.loads(topics_example_path().read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw_topics = None

    if isinstance(raw_topics, dict):
        raw_topics = raw_topics.get("topics", [])
    if not isinstance(raw_topics, list) or not raw_topics:
        raw_topics = DEFAULT_TOPIC_DEFINITIONS

    definitions: list[dict[str, str]] = []
    for item in raw_topics:
        if not isinstance(item, dict):
            continue
        topic_key = str(item.get("key", "")).strip() or str(item.get("topic_key", "")).strip()
        title = str(item.get("title", "")).strip()
        if not topic_key or not title:
            continue
        definitions.append(
            {
                "key": topic_key,
                "title": title,
                "purpose": str(item.get("purpose", topic_key)).strip() or topic_key,
                "lane": str(item.get("lane", "")).strip(),
                "emoji": str(item.get("emoji", "")).strip(),
                "seed_text": str(item.get("seed_text", f"Rick topic online: {title}")).strip(),
            }
        )
    return definitions or list(DEFAULT_TOPIC_DEFINITIONS)


def topic_seed_text(topic_key: str, title: str = "") -> str:
    for item in load_topic_definitions():
        if item["key"] == topic_key:
            return item["seed_text"]
    if title:
        return f"Rick topic online: {title}"
    return "Rick topic online."


def get_topic_by_thread(connection: sqlite3.Connection, chat_id: str, thread_id: int | None) -> sqlite3.Row | None:
    if thread_id is None:
        return None
    return connection.execute(
        """
        SELECT *
        FROM telegram_topics
        WHERE chat_id = ? AND thread_id = ?
        LIMIT 1
        """,
        (str(chat_id).strip(), int(thread_id)),
    ).fetchone()


def get_topic_by_key(connection: sqlite3.Connection, topic_key: str, chat_id: str | None = None) -> sqlite3.Row | None:
    resolved_chat = str(chat_id or forum_chat_id()).strip()
    if not resolved_chat:
        return None
    return connection.execute(
        """
        SELECT *
        FROM telegram_topics
        WHERE chat_id = ? AND topic_key = ?
        LIMIT 1
        """,
        (resolved_chat, topic_key),
    ).fetchone()


def get_topic_for_workflow(connection: sqlite3.Connection, workflow_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM telegram_topics
        WHERE workflow_id = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (workflow_id,),
    ).fetchone()


def workflow_session_key(connection: sqlite3.Connection, workflow_id: str | None) -> str:
    if not workflow_id:
        return ""
    row = connection.execute(
        "SELECT openclaw_session_key FROM workflows WHERE id = ?",
        (workflow_id,),
    ).fetchone()
    if row is None:
        return ""
    return str(row["openclaw_session_key"] or "").strip()


def topic_with_workflow_metadata(connection: sqlite3.Connection, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["openclaw_session_key"] = workflow_session_key(connection, payload.get("workflow_id"))
    return payload


def list_telegram_topics(connection: sqlite3.Connection, chat_id: str | None = None) -> list[dict[str, Any]]:
    if chat_id:
        rows = connection.execute(
            """
            SELECT *
            FROM telegram_topics
            WHERE chat_id = ?
            ORDER BY source ASC, purpose ASC, title ASC
            """,
            (str(chat_id).strip(),),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT *
            FROM telegram_topics
            ORDER BY chat_id ASC, source ASC, purpose ASC, title ASC
            """
        ).fetchall()
    return [topic_with_workflow_metadata(connection, row) for row in rows]


def touch_topic(connection: sqlite3.Connection, chat_id: str, thread_id: int | None) -> None:
    if thread_id is None:
        return
    connection.execute(
        """
        UPDATE telegram_topics
        SET updated_at = ?, last_seen_at = ?
        WHERE chat_id = ? AND thread_id = ?
        """,
        (now_iso(), now_iso(), str(chat_id).strip(), int(thread_id)),
    )


def upsert_telegram_topic(
    connection: sqlite3.Connection,
    *,
    chat_id: str,
    thread_id: int,
    topic_key: str,
    title: str,
    purpose: str,
    lane: str = "",
    workflow_id: str | None = None,
    status: str = "active",
    icon_custom_emoji_id: str = "",
    source: str = "manual",
    seed_message_id: int | None = None,
) -> dict[str, Any]:
    stamp = now_iso()
    normalized_chat = str(chat_id).strip()
    normalized_thread = int(thread_id)
    normalized_key = topic_key.strip()
    normalized_slug = normalized_key.replace(":", "-")

    existing = connection.execute(
        """
        SELECT *
        FROM telegram_topics
        WHERE chat_id = ? AND (thread_id = ? OR topic_key = ?)
        ORDER BY CASE WHEN thread_id = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (normalized_chat, normalized_thread, normalized_key, normalized_thread),
    ).fetchone()

    if existing is None:
        connection.execute(
            """
            INSERT INTO telegram_topics (
                chat_id, thread_id, topic_key, slug, title, purpose, lane, workflow_id, status,
                icon_custom_emoji_id, source, seed_message_id, created_at, updated_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_chat,
                normalized_thread,
                normalized_key,
                normalized_slug,
                title,
                purpose,
                lane,
                workflow_id,
                status,
                icon_custom_emoji_id,
                source,
                seed_message_id,
                stamp,
                stamp,
                stamp,
            ),
        )
    else:
        connection.execute(
            """
            UPDATE telegram_topics
            SET thread_id = ?,
                topic_key = ?,
                slug = ?,
                title = ?,
                purpose = ?,
                lane = ?,
                workflow_id = ?,
                status = ?,
                icon_custom_emoji_id = ?,
                source = ?,
                seed_message_id = COALESCE(?, seed_message_id),
                updated_at = ?,
                last_seen_at = ?
            WHERE id = ?
            """,
            (
                normalized_thread,
                normalized_key,
                normalized_slug,
                title,
                purpose,
                lane,
                workflow_id,
                status,
                icon_custom_emoji_id,
                source,
                seed_message_id,
                stamp,
                stamp,
                existing["id"],
            ),
        )

    row = connection.execute(
        """
        SELECT *
        FROM telegram_topics
        WHERE chat_id = ? AND thread_id = ?
        LIMIT 1
        """,
        (normalized_chat, normalized_thread),
    ).fetchone()
    return dict(row) if row is not None else {}


def bind_workflow_topic(
    connection: sqlite3.Connection,
    workflow_id: str,
    *,
    chat_id: str,
    thread_id: int,
    topic_key: str,
    title: str,
    purpose: str = "workflow",
    lane: str = "",
    status: str = "active",
    icon_custom_emoji_id: str = "",
    source: str = "manual",
    seed_message_id: int | None = None,
) -> dict[str, Any]:
    topic = upsert_telegram_topic(
        connection,
        chat_id=chat_id,
        thread_id=thread_id,
        topic_key=topic_key,
        title=title,
        purpose=purpose,
        lane=lane,
        workflow_id=workflow_id,
        status=status,
        icon_custom_emoji_id=icon_custom_emoji_id,
        source=source,
        seed_message_id=seed_message_id,
    )
    session_key = format_openclaw_session_key(chat_id, thread_id)
    connection.execute(
        "UPDATE workflows SET telegram_target = ?, openclaw_session_key = ?, updated_at = ? WHERE id = ?",
        (format_telegram_target(chat_id, thread_id), session_key, now_iso(), workflow_id),
    )
    topic["openclaw_session_key"] = session_key
    return topic


def unbind_workflow_topic(
    connection: sqlite3.Connection,
    *,
    chat_id: str,
    thread_id: int,
) -> dict[str, Any] | None:
    row = get_topic_by_thread(connection, chat_id, thread_id)
    if row is None:
        return None
    workflow_id = row["workflow_id"]
    connection.execute(
        """
        UPDATE telegram_topics
        SET workflow_id = NULL, status = 'active', updated_at = ?, last_seen_at = ?
        WHERE id = ?
        """,
        (now_iso(), now_iso(), row["id"]),
    )
    if workflow_id:
        target = format_telegram_target(chat_id, thread_id)
        connection.execute(
            """
            UPDATE workflows
            SET telegram_target = CASE WHEN telegram_target = ? THEN '' ELSE telegram_target END,
                openclaw_session_key = CASE WHEN telegram_target = ? THEN '' ELSE openclaw_session_key END,
                updated_at = ?
            WHERE id = ?
            """,
            (target, target, now_iso(), workflow_id),
        )
    updated = get_topic_by_thread(connection, chat_id, thread_id)
    return topic_with_workflow_metadata(connection, updated) if updated is not None else None


def workflow_target(connection: sqlite3.Connection, workflow_id: str) -> TelegramTarget | None:
    row = connection.execute("SELECT telegram_target FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
    if row is None:
        return None
    parsed = parse_telegram_target(row["telegram_target"])
    if parsed is not None:
        return parsed
    topic = get_topic_for_workflow(connection, workflow_id)
    if topic is None:
        return None
    return TelegramTarget(chat_id=str(topic["chat_id"]), thread_id=int(topic["thread_id"]))


def topic_for_purpose(connection: sqlite3.Connection, purpose: str) -> sqlite3.Row | None:
    topic_key = PURPOSE_TOPIC_KEYS.get(purpose.strip().lower())
    if not topic_key:
        return None
    return get_topic_by_key(connection, topic_key)


def topic_for_lane(connection: sqlite3.Connection, lane: str) -> sqlite3.Row | None:
    topic_key = LANE_TOPIC_KEYS.get(lane.strip())
    if not topic_key:
        return None
    return get_topic_by_key(connection, topic_key)


def resolve_notification_target(
    connection: sqlite3.Connection,
    *,
    workflow_id: str | None = None,
    lane: str = "",
    purpose: str = "",
    topic_key: str = "",
    chat_id: str = "",
    thread_id: int | None = None,
) -> TelegramTarget | None:
    if chat_id.strip():
        return TelegramTarget(chat_id=chat_id.strip(), thread_id=thread_id)

    if workflow_id:
        target = workflow_target(connection, workflow_id)
        if target is not None:
            return target

    if thread_mode_enabled():
        # Direct topic_key lookup — highest specificity
        if topic_key:
            topic = get_topic_by_key(connection, topic_key)
            if topic is not None:
                return TelegramTarget(chat_id=str(topic["chat_id"]), thread_id=int(topic["thread_id"]))

        if purpose:
            topic = topic_for_purpose(connection, purpose)
            if topic is not None:
                return TelegramTarget(chat_id=str(topic["chat_id"]), thread_id=int(topic["thread_id"]))

        if lane:
            topic = topic_for_lane(connection, lane)
            if topic is not None:
                return TelegramTarget(chat_id=str(topic["chat_id"]), thread_id=int(topic["thread_id"]))

    fallback_chat = allowed_chat_id() or forum_chat_id()
    if not fallback_chat:
        return None
    return TelegramTarget(chat_id=fallback_chat, thread_id=None)


def topic_registry_markdown(connection: sqlite3.Connection, chat_id: str | None = None) -> str:
    rows = list_telegram_topics(connection, chat_id=chat_id)
    lines = [
        "# Telegram Topics",
        "",
        f"Generated: {now_iso()}",
        "",
        "| Topic | Key | Purpose | Lane | Chat | Thread | Workflow | OpenClaw Session | Source | Status |",
        "|-------|-----|---------|------|------|--------|----------|------------------|--------|--------|",
    ]
    if not rows:
        lines.append("| none | - | - | - | - | - | - | - | - | - |")
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("title", "")),
                    str(row.get("topic_key", "")),
                    str(row.get("purpose", "")),
                    str(row.get("lane", "")),
                    str(row.get("chat_id", "")),
                    str(row.get("thread_id", "")),
                    str(row.get("workflow_id", "") or "-"),
                    str(row.get("openclaw_session_key", "") or "-"),
                    str(row.get("source", "")),
                    str(row.get("status", "")),
                ]
            )
            + " |"
        )
    return "\n".join(lines).rstrip() + "\n"


def write_topic_registry_markdown(connection: sqlite3.Connection) -> Path:
    TOPIC_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOPIC_REPORT_FILE.write_text(topic_registry_markdown(connection), encoding="utf-8")
    return TOPIC_REPORT_FILE


def workflow_topic_title(title: str, workflow_id: str) -> str:
    normalized = (title or workflow_id).strip() or workflow_id
    if len(normalized) <= 120:
        return normalized
    return normalized[:117].rstrip() + "..."


def workflow_topic_key(workflow_id: str) -> str:
    return f"workflow:{workflow_id}"
