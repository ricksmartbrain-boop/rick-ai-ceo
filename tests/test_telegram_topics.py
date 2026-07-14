from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class TelegramThreadModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tempdir.name)
        self.env_backup = os.environ.copy()
        os.environ.update(
            {
                "RICK_DATA_ROOT": str(self.data_root),
                "RICK_RUNTIME_DB_FILE": str(self.data_root / "runtime" / "rick-runtime.db"),
                "RICK_EXECUTION_LEDGER_FILE": str(self.data_root / "operations" / "execution-ledger.jsonl"),
                "RICK_LLM_USAGE_LOG_FILE": str(self.data_root / "operations" / "llm-usage.jsonl"),
                "RICK_PORTFOLIO_SCORECARDS_FILE": str(ROOT_DIR / "config" / "portfolio-scorecards.example.json"),
                "RICK_LANE_POLICY_FILE": str(ROOT_DIR / "config" / "lane-policy.example.json"),
                "RICK_TELEGRAM_BOT_TOKEN": "telegram-test-token",
                "RICK_TELEGRAM_ALLOWED_CHAT_ID": "-1009000",
                "RICK_TELEGRAM_FORUM_CHAT_ID": "-1009000",
                "RICK_TELEGRAM_THREAD_MODE": "hybrid",
                "RICK_TELEGRAM_TOPICS_FILE": str(ROOT_DIR / "config" / "telegram-topics.example.json"),
                "SUBSTACK_PUBLICATION": "rickbuilds",
                "SUBSTACK_SESSION_COOKIE": "test-cookie",
                "LINKEDIN_ACCESS_TOKEN": "linkedin-token",
                "LINKEDIN_PERSON_URN": "urn:li:person:test",
                "RICK_XPOST_BIN": "echo",
                "RICK_PUBLIC_AUTHOR": "Rick",
                "RICK_BRAND_BLURB": "Autonomous operator building public revenue systems.",
            }
        )
        self.db, self.engine, self.telegram_topics, self.llm = self.load_runtime_modules()
        self.connection = self.db.connect()
        self.db.init_db(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        os.environ.clear()
        os.environ.update(self.env_backup)
        self.tempdir.cleanup()

    def load_runtime_modules(self):
        import runtime.db as db
        import runtime.engine as engine
        import runtime.llm as llm
        import runtime.telegram_topics as telegram_topics

        for module in (db, telegram_topics, llm, engine):
            importlib.reload(module)
        return db, engine, telegram_topics, llm

    def fake_generation(self, route: str, prompt: str, fallback: str):
        return self.llm.GenerationResult(
            content=f"# {route.title()}\n\nSynthetic content for tests.\n",
            route=route,
            model="test-model",
            runner="test",
            mode="test",
        )

    def fake_run_command(self, command: list[str], env: dict[str, str] | None = None, cwd: Path | None = None):
        joined = " ".join(command)
        if "create-outline.sh" in joined:
            return subprocess.CompletedProcess(command, 0, stdout="# Outline\n\n- Part 1\n", stderr="")

        if "create-product.sh" in joined:
            name = command[command.index("--name") + 1]
            slug = self.engine.slugify(name)
            project_dir = self.data_root / "projects" / slug
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "stripe-product.json").write_text(
                json.dumps(
                    {
                        "product_id": "prod_123",
                        "price_id": "price_123",
                        "status": "checkout-ready",
                        "payment_link_url": "https://buy.rick.ai/checkout",
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="created product\n", stderr="")

        if "create-landing-page.sh" in joined:
            output_dir = Path(command[command.index("--output") + 1])
            (output_dir / "app").mkdir(parents=True, exist_ok=True)
            (output_dir / "app" / "page.tsx").write_text(
                "export default function Page() { return null }\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="landing page created\n", stderr="")

        if "newsletter-send.sh" in joined:
            return subprocess.CompletedProcess(command, 0, stdout="Scheduled edition\n", stderr="")

        if "social-post.sh" in joined:
            return subprocess.CompletedProcess(command, 0, stdout="Posted social update\n", stderr="")

        if command and Path(command[0]).name == "echo":
            return subprocess.CompletedProcess(command, 0, stdout="Posted to X\n", stderr="")

        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    def test_queue_from_fixed_topic_creates_workflow_topic(self) -> None:
        self.telegram_topics.upsert_telegram_topic(
            self.connection,
            chat_id="-1009000",
            thread_id=12,
            topic_key="product-lab",
            title="Product Lab",
            purpose="product-lab",
            lane="product-lane",
            source="fixed",
        )
        self.connection.commit()

        with patch.object(self.engine, "notify_operator", return_value=None):
            reply = self.engine.parse_telegram_text(
                self.connection,
                '/queue "Revenue Agent OS" --price 49 --type guide',
                chat_id="-1009000",
                thread_id=12,
                is_forum=True,
            )

        self.assertIn("Queued info product workflow wf_", reply)
        workflows = self.engine.status_summary(self.connection)["workflows"]
        self.assertEqual(len(workflows), 1)
        self.assertIn("Revenue Agent OS", workflows[0]["title"])

    def test_status_in_workflow_topic_returns_workflow_summary(self) -> None:
        workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Context Aware Launches",
            price_usd=29,
            product_type="guide",
        )
        self.telegram_topics.bind_workflow_topic(
            self.connection,
            workflow_id,
            chat_id="-1009000",
            thread_id=77,
            topic_key=f"workflow:{workflow_id}",
            title="Context Aware Launches",
            purpose="workflow",
            lane="product-lane",
            source="workflow",
        )
        self.connection.commit()

        reply = self.engine.parse_telegram_text(self.connection, "/status", chat_id="-1009000", thread_id=77, is_forum=True)
        self.assertIn(workflow_id, reply)
        self.assertIn("Status: queued", reply)
        self.assertIn("Telegram: -1009000:topic:77", reply)
        self.assertIn("OpenClaw Session: agent:rick:telegram:group:-1009000:topic:77", reply)

    def test_approval_and_resolution_notifications_stay_in_workflow_topic(self) -> None:
        workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Approval Routing Product",
            price_usd=29,
            product_type="guide",
        )
        self.telegram_topics.bind_workflow_topic(
            self.connection,
            workflow_id,
            chat_id="-1009000",
            thread_id=88,
            topic_key=f"workflow:{workflow_id}",
            title="Approval Routing Product",
            purpose="workflow",
            lane="product-lane",
            source="workflow",
        )
        self.connection.commit()

        notify_calls: list[str] = []

        def capture_notify(connection, text: str, **kwargs):
            notify_calls.append(text)

        with patch.object(self.engine, "generate_text", side_effect=self.fake_generation), patch.object(
            self.engine, "run_command", side_effect=self.fake_run_command
        ), patch.object(self.engine, "notify_operator", side_effect=capture_notify):
            self.engine.work(self.connection, limit=20)

        self.assertTrue(any("needs approval" in msg for msg in notify_calls))

        approval_id = self.engine.status_summary(self.connection, workflow_id=workflow_id)["approvals"][0]["id"]
        with patch.object(self.engine, "notify_operator", side_effect=capture_notify):
            result = self.engine.resolve_approval(self.connection, approval_id, "approved", "looks good", "telegram")

        self.assertEqual(result["status"], "approved")
        self.assertTrue(any("Approval accepted" in msg for msg in notify_calls))

    def test_bind_here_attaches_manual_topic(self) -> None:
        workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Manual Topic Attach",
            price_usd=19,
            product_type="guide",
        )
        self.telegram_topics.upsert_telegram_topic(
            self.connection,
            chat_id="-1009000",
            thread_id=55,
            topic_key="manual:-1009000:55",
            title="Launch Topic",
            purpose="workflow",
            lane="product-lane",
            source="manual",
        )
        self.connection.commit()

        reply = self.engine.parse_telegram_text(
            self.connection,
            f"/bind here {workflow_id}",
            chat_id="-1009000",
            thread_id=55,
            is_forum=True,
        )

        self.assertIn("Bound this topic", reply)
        workflow = self.engine.get_workflow(self.connection, workflow_id)
        self.assertEqual(workflow["telegram_target"], "-1009000:topic:55")
        self.assertEqual(workflow["openclaw_session_key"], "agent:rick:telegram:group:-1009000:topic:55")
        topic = self.telegram_topics.get_topic_for_workflow(self.connection, workflow_id)
        self.assertIsNotNone(topic)
        self.assertEqual(topic["thread_id"], 55)

    def test_unbind_here_clears_openclaw_session_key(self) -> None:
        workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Topic Unbind",
            price_usd=29,
            product_type="guide",
        )
        self.telegram_topics.bind_workflow_topic(
            self.connection,
            workflow_id,
            chat_id="-1009000",
            thread_id=66,
            topic_key=f"workflow:{workflow_id}",
            title="Topic Unbind",
            purpose="workflow",
            lane="product-lane",
            source="workflow",
        )
        self.connection.commit()

        reply = self.engine.parse_telegram_text(
            self.connection,
            "/unbind here",
            chat_id="-1009000",
            thread_id=66,
            is_forum=True,
        )

        self.assertIn("Unbound workflow", reply)
        workflow = self.engine.get_workflow(self.connection, workflow_id)
        self.assertEqual(workflow["telegram_target"], "")
        self.assertEqual(workflow["openclaw_session_key"], "")

    def test_thread_mode_off_keeps_single_chat_behavior(self) -> None:
        os.environ["RICK_TELEGRAM_THREAD_MODE"] = "off"
        self.db, self.engine, self.telegram_topics, self.llm = self.load_runtime_modules()
        self.connection.close()
        self.connection = self.db.connect()
        self.db.init_db(self.connection)
        self.telegram_topics.upsert_telegram_topic(
            self.connection,
            chat_id="-1009000",
            thread_id=12,
            topic_key="product-lab",
            title="Product Lab",
            purpose="product-lab",
            lane="product-lane",
            source="fixed",
        )
        self.connection.commit()

        reply = self.engine.parse_telegram_text(
            self.connection,
            '/queue "Single Chat Flow" --price 29 --type guide',
            chat_id="-1009000",
            thread_id=12,
            is_forum=True,
        )

        self.assertIn("Queued info product workflow", reply)
        self.assertNotIn("Topic:", reply)
        workflow_id = self.engine.status_summary(self.connection)["workflows"][0]["id"]
        workflow = self.engine.get_workflow(self.connection, workflow_id)
        self.assertEqual(workflow["telegram_target"], "")


if __name__ == "__main__":
    unittest.main()
