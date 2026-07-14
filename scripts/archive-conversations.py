#!/usr/bin/env python3
"""Archive conversation messages from runtime DB to Obsidian vault for memory indexing."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
ARCHIVE_DIR = DATA_ROOT / "memory" / "conversations"


def archive_conversations():
    """Archive yesterday's conversation messages to vault markdown files."""
    from runtime.db import connect, init_db

    conn = connect()
    init_db(conn)

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Get distinct topics from yesterday
    try:
        topics = conn.execute(
            """
            SELECT DISTINCT topic_key, chat_id, thread_id
            FROM conversation_messages
            WHERE created_at >= ? AND created_at < ?
            AND topic_key != ''
            """,
            (yesterday, (datetime.now()).strftime("%Y-%m-%d")),
        ).fetchall()
    except Exception as exc:
        print(f"No conversation_messages table or error: {exc}", file=sys.stderr)
        return

    if not topics:
        print(f"No conversations from {yesterday}.")
        return

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archived = 0

    for topic_row in topics:
        topic_key = topic_row["topic_key"]
        messages = conn.execute(
            """
            SELECT direction, sender, message_text, created_at
            FROM conversation_messages
            WHERE topic_key = ? AND created_at >= ? AND created_at < ?
            ORDER BY created_at ASC
            """,
            (topic_key, yesterday, datetime.now().strftime("%Y-%m-%d")),
        ).fetchall()

        if not messages:
            continue

        safe_key = topic_key.replace("/", "-").replace(":", "-")
        filepath = ARCHIVE_DIR / f"{yesterday}-{safe_key}.md"

        lines = [
            "---",
            f"type: conversation-archive",
            f"date: {yesterday}",
            f"topic: {topic_key}",
            f"messages: {len(messages)}",
            f"tags: [conversation, {safe_key}]",
            f"tier: cold",
            "---",
            "",
            f"# Conversation: {topic_key} — {yesterday}",
            "",
        ]

        for msg in messages:
            direction = "→" if msg["direction"] == "outbound" else "←"
            time_str = msg["created_at"].split("T")[1] if "T" in msg["created_at"] else ""
            lines.append(f"**{time_str}** {direction} **{msg['sender']}**: {msg['message_text'][:500]}")
            lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        archived += 1
        print(f"Archived {len(messages)} messages for topic {topic_key}")

    conn.close()
    print(f"Archived {archived} conversation(s) from {yesterday}.")


if __name__ == "__main__":
    archive_conversations()
