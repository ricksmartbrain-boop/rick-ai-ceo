#!/usr/bin/env python3
"""CLI entrypoint for Rick v6 runtime."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

# Sentry error tracking — init before everything else
import sentry_sdk
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN", ""),
    environment="production",
    send_default_pii=False,
    traces_sample_rate=0.1,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from runtime.db import connect, init_db, runtime_db_path
from runtime.engine import (
    enqueue_publish_bundle,
    get_workflow,
    heartbeat,
    parse_telegram_text,
    queue_initiative_workflow,
    queue_post_purchase_workflow,
    queue_info_product_workflow,
    resolve_approval,
    status_summary,
    validate_config,
    work,
)
from runtime.telegram_topics import (
    bind_workflow_topic,
    forum_chat_id,
    get_topic_by_thread,
    list_telegram_topics,
    write_topic_registry_markdown,
)


def print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Rick v6 runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize the runtime database")

    queue_parser = subparsers.add_parser("queue-info-product", help="Queue a new info product workflow")
    queue_parser.add_argument("--idea", required=True)
    queue_parser.add_argument("--price-usd", required=True, type=int)
    queue_parser.add_argument("--product-type", default="guide", choices=["guide", "mini-course", "full-course"])
    queue_parser.add_argument("--audience", default="")
    queue_parser.add_argument("--unique-angle", default="")
    queue_parser.add_argument("--project", default="info-products")

    purchase_parser = subparsers.add_parser("record-purchase", help="Queue post-purchase fulfillment for a buyer")
    purchase_parser.add_argument("--workflow-id", required=True, help="Source product workflow id")
    purchase_parser.add_argument("--email", required=True, help="Buyer email")
    purchase_parser.add_argument("--customer-name", default="", help="Buyer name")
    purchase_parser.add_argument("--payment-id", default="", help="Stripe/payment id")
    purchase_parser.add_argument("--amount-usd", default=0.0, type=float, help="Purchase amount in USD")
    purchase_parser.add_argument("--delivery-url", required=True, help="Public delivery URL for the purchased product")
    purchase_parser.add_argument("--source", default="manual", help="stripe, manual, import, etc.")

    work_parser = subparsers.add_parser("work", help="Process queued jobs")
    work_parser.add_argument("--limit", type=int, default=1)

    heartbeat_parser = subparsers.add_parser("heartbeat", help="Return runtime heartbeat summary")
    heartbeat_parser.add_argument("--work-limit", type=int, default=0, help="Also process up to N jobs after heartbeat")

    approve_parser = subparsers.add_parser("approve", help="Approve a pending founder approval")
    approve_parser.add_argument("--approval-id", required=True)
    approve_parser.add_argument("--note", default="")
    approve_parser.add_argument("--actor", default="cli")

    deny_parser = subparsers.add_parser("deny", help="Deny a pending founder approval")
    deny_parser.add_argument("--approval-id", required=True)
    deny_parser.add_argument("--note", default="")
    deny_parser.add_argument("--actor", default="cli")

    publish_parser = subparsers.add_parser("publish", help="Queue publish jobs for a launch-ready workflow")
    publish_parser.add_argument("--workflow-id", required=True)
    publish_parser.add_argument("--channels", default="newsletter,linkedin,x")

    initiative_parser = subparsers.add_parser("queue-initiative", help="Queue a new initiative workflow")
    initiative_parser.add_argument("--objective", required=True, help="Initiative objective")
    initiative_parser.add_argument("--project", default="rick-v6", help="Project name")
    initiative_parser.add_argument("--priority", type=int, default=40, help="Priority (lower = higher)")

    status_parser = subparsers.add_parser("status", help="Show runtime status")
    status_parser.add_argument("--workflow-id")

    telegram_parser = subparsers.add_parser("telegram", help="Parse a Telegram-style command")
    telegram_parser.add_argument("--text", required=True)
    telegram_parser.add_argument("--chat-id", default="")
    telegram_parser.add_argument("--thread-id", type=int)
    telegram_parser.add_argument("--message-id", type=int)
    telegram_parser.add_argument("--is-forum", action="store_true")

    telegram_topics_parser = subparsers.add_parser("telegram-topics", help="Manage Telegram forum topics")
    telegram_topics_subparsers = telegram_topics_parser.add_subparsers(dest="telegram_topics_command", required=True)

    telegram_topics_list = telegram_topics_subparsers.add_parser("list", help="List persisted Telegram topics")
    telegram_topics_list.add_argument("--chat-id", default="")

    telegram_topics_bind = telegram_topics_subparsers.add_parser("bind", help="Bind a workflow to an existing Telegram topic")
    telegram_topics_bind.add_argument("--workflow-id", required=True)
    telegram_topics_bind.add_argument("--thread-id", required=True, type=int)
    telegram_topics_bind.add_argument("--chat-id", default="")

    args = parser.parse_args()
    connection = connect()
    try:
        return _run(connection, args)
    finally:
        connection.close()


def _run(connection: sqlite3.Connection, args: argparse.Namespace) -> int:
    init_db(connection)

    for warning in validate_config():
        print(f"[config] {warning}", file=sys.stderr)

    if args.command == "init":
        print(f"Initialized runtime DB at {runtime_db_path()}")
        return 0

    if args.command == "queue-info-product":
        workflow_id = queue_info_product_workflow(
            connection,
            idea=args.idea,
            price_usd=args.price_usd,
            product_type=args.product_type,
            audience=args.audience,
            unique_angle=args.unique_angle,
            project=args.project,
        )
        print_json({"workflow_id": workflow_id, "db": str(runtime_db_path())})
        return 0

    if args.command == "record-purchase":
        workflow_id = queue_post_purchase_workflow(
            connection,
            source_workflow_id=args.workflow_id,
            email=args.email,
            customer_name=args.customer_name,
            payment_id=args.payment_id,
            amount_usd=args.amount_usd,
            delivery_url=args.delivery_url,
            source=args.source,
        )
        print_json({"workflow_id": workflow_id, "db": str(runtime_db_path())})
        return 0

    if args.command == "work":
        print_json(work(connection, limit=args.limit))
        return 0

    if args.command == "heartbeat":
        summary = heartbeat(connection)
        if args.work_limit > 0:
            summary["work_results"] = work(connection, limit=args.work_limit)
        print_json(summary)
        return 0

    if args.command == "approve":
        print_json(resolve_approval(connection, args.approval_id, "approved", args.note, args.actor))
        return 0

    if args.command == "deny":
        print_json(resolve_approval(connection, args.approval_id, "denied", args.note, args.actor))
        return 0

    if args.command == "publish":
        channels = [item.strip() for item in args.channels.split(",") if item.strip()]
        print_json(enqueue_publish_bundle(connection, args.workflow_id, channels))
        return 0

    if args.command == "queue-initiative":
        workflow_id = queue_initiative_workflow(
            connection,
            objective=args.objective,
            project=args.project,
            priority=args.priority,
        )
        print_json({"workflow_id": workflow_id, "kind": "initiative", "db": str(runtime_db_path())})
        return 0

    if args.command == "status":
        print_json(status_summary(connection, workflow_id=args.workflow_id))
        return 0

    if args.command == "telegram":
        print(
            parse_telegram_text(
                connection,
                args.text,
                chat_id=args.chat_id,
                thread_id=args.thread_id,
                message_id=args.message_id,
                is_forum=args.is_forum,
            )
        )
        return 0

    if args.command == "telegram-topics":
        if args.telegram_topics_command == "list":
            print_json({"topics": list_telegram_topics(connection, chat_id=args.chat_id or None)})
            return 0
        if args.telegram_topics_command == "bind":
            workflow = get_workflow(connection, args.workflow_id)
            resolved_chat = str(args.chat_id or forum_chat_id()).strip()
            current_topic = get_topic_by_thread(connection, resolved_chat, args.thread_id)
            topic = bind_workflow_topic(
                connection,
                workflow["id"],
                chat_id=resolved_chat,
                thread_id=args.thread_id,
                topic_key=(str(current_topic["topic_key"]) if current_topic is not None else f"manual:{resolved_chat}:{args.thread_id}"),
                title=(str(current_topic["title"]) if current_topic is not None else f"Topic {args.thread_id}"),
                purpose="workflow",
                lane=str(workflow["lane"]),
                status="active",
                icon_custom_emoji_id=(str(current_topic["icon_custom_emoji_id"]) if current_topic is not None else ""),
                source=(str(current_topic["source"]) if current_topic is not None else "manual"),
                seed_message_id=(int(current_topic["seed_message_id"]) if current_topic is not None and current_topic["seed_message_id"] is not None else None),
            )
            write_topic_registry_markdown(connection)
            connection.commit()
            print_json({"topic": topic, "workflow_id": workflow["id"]})
            return 0

    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        import traceback
        from datetime import datetime as _dt

        ts = _dt.now().isoformat(timespec="seconds")
        sys.stderr.write(f"[{ts}] runner fatal: {exc}\n")
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)
