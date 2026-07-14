from __future__ import annotations

import importlib
import importlib.util
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


class RuntimeWorkflowTests(unittest.TestCase):
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
                "RICK_TELEGRAM_ALLOWED_CHAT_ID": "12345",
                "SUBSTACK_PUBLICATION": "rickbuilds",
                "SUBSTACK_SESSION_COOKIE": "test-cookie",
                "LINKEDIN_ACCESS_TOKEN": "linkedin-token",
                "LINKEDIN_PERSON_URN": "urn:li:person:test",
                "RICK_XPOST_BIN": "echo",
                "RICK_PUBLIC_AUTHOR": "Rick",
                "RICK_BRAND_BLURB": "Autonomous operator building public revenue systems.",
                "BEEHIIV_API_KEY": "test-beehiiv-key",
                "BEEHIIV_PUB_ID": "pub_test123",
            }
        )
        self.db, self.llm, self.engine = self.load_runtime_modules()
        self.connection = self.db.connect()
        self.db.init_db(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        os.environ.clear()
        os.environ.update(self.env_backup)
        self.tempdir.cleanup()

    def load_runtime_modules(self):
        import runtime.context as context
        import runtime.db as db
        import runtime.engine as engine
        import runtime.llm as llm

        for module in (db, context, llm, engine):
            importlib.reload(module)
        return db, llm, engine

    def load_script_module(self, name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module

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

    def fake_run_command_manual_launch_path(self, command: list[str], env: dict[str, str] | None = None, cwd: Path | None = None):
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
                        "product_id": "",
                        "price_id": "",
                        "payment_link_url": "",
                        "status": "manual-required",
                        "next_step": "Create the product and price in Stripe, then replace the empty ids.",
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="manual scaffold\n", stderr="")

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

    def load_sequencer_module(self):
        return self.load_script_module("runtime_sequencer_test", ROOT_DIR / "runtime" / "sequencer.py")

    def insert_workflow(self, *, workflow_id: str, stage: str, context: dict, created_at: str | None = None) -> None:
        ts = created_at or "2026-05-02T03:28:00"
        self.connection.execute(
            """
            INSERT INTO workflows
              (id, kind, title, slug, project, status, stage, priority, owner, lane,
               telegram_target, openclaw_session_key, context_json, created_at, updated_at,
               started_at, finished_at)
            VALUES (?, 'qualified_lead', ?, ?, 'growth', 'active', ?, 50, 'rick', 'customer-lane',
                    '', '', ?, ?, ?, NULL, NULL)
            """,
            (
                workflow_id,
                workflow_id,
                workflow_id.replace("_", "-"),
                stage,
                json.dumps(context),
                ts,
                ts,
            ),
        )
        self.connection.commit()

    def test_info_product_workflow_reaches_launch_ready(self) -> None:
        workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Autonomous Revenue OS",
            price_usd=29,
            product_type="guide",
        )

        with patch.object(self.engine, "generate_text", side_effect=self.fake_generation), patch.object(
            self.engine, "run_command", side_effect=self.fake_run_command
        ), patch.object(self.engine, "notify_operator", return_value=None):
            results = self.engine.work(self.connection, limit=20)

        self.assertTrue(results)
        status = self.engine.status_summary(self.connection, workflow_id=workflow_id)
        self.assertEqual(status["workflow"]["status"], "blocked")
        self.assertEqual(status["workflow"]["stage"], "awaiting-approval")
        self.assertEqual(len(status["approvals"]), 1)

        approval_id = status["approvals"][0]["id"]
        with patch.object(self.engine, "notify_operator", return_value=None):
            resolution = self.engine.resolve_approval(self.connection, approval_id, "approved", "looks good", "telegram")
        self.assertEqual(resolution["status"], "approved")

        with patch.object(self.engine, "generate_text", side_effect=self.fake_generation), patch.object(
            self.engine, "run_command", side_effect=self.fake_run_command
        ), patch.object(self.engine, "notify_operator", return_value=None):
            self.engine.work(self.connection, limit=3)

        status = self.engine.status_summary(self.connection, workflow_id=workflow_id)
        self.assertEqual(status["workflow"]["status"], "launch-ready")
        self.assertEqual(status["workflow"]["stage"], "launch-ready")

    def test_non_owner_actor_can_only_resolve_synthetic_approvals(self) -> None:
        # WHY: on 2026-07-13 an automated "heartbeat-cleanup" session resolved a
        # real approval (apr_62dafa5c3a3f). Non-owner actors must be refused on
        # anything without a synthetic marker, so real owner-pending approvals
        # (e.g. warm-revival sends awaiting Vlad) can never be auto-touched.
        workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Autonomous Revenue OS",
            price_usd=29,
            product_type="guide",
        )
        with patch.object(self.engine, "generate_text", side_effect=self.fake_generation), patch.object(
            self.engine, "run_command", side_effect=self.fake_run_command
        ), patch.object(self.engine, "notify_operator", return_value=None):
            self.engine.work(self.connection, limit=20)
        approval_id = self.engine.status_summary(self.connection, workflow_id=workflow_id)["approvals"][0]["id"]

        # Non-owner actor on a real (non-synthetic) approval → refused, untouched.
        with patch.object(self.engine, "notify_operator", return_value=None):
            refused = self.engine.resolve_approval(self.connection, approval_id, "denied", "stale cleanup", "iris")
        self.assertEqual(refused["status"], "refused")
        row = self.connection.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        self.assertEqual(row["status"], "open")
        events = self.connection.execute(
            "SELECT COUNT(*) AS c FROM events WHERE event_type = 'guard_refused_non_owner'"
        ).fetchone()
        self.assertEqual(events["c"], 1)

        # Same actor on a verifiably synthetic approval → allowed.
        self.connection.execute(
            "UPDATE approvals SET request_text = request_text || ' [DRILL] canary' WHERE id = ?",
            (approval_id,),
        )
        self.connection.commit()
        with patch.object(self.engine, "notify_operator", return_value=None):
            resolution = self.engine.resolve_approval(self.connection, approval_id, "denied", "drill cleanup", "iris")
        self.assertEqual(resolution["status"], "denied")

    def test_publish_bundle_marks_workflow_published(self) -> None:
        workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Agent Launch Manual",
            price_usd=49,
            product_type="guide",
        )

        with patch.object(self.engine, "generate_text", side_effect=self.fake_generation), patch.object(
            self.engine, "run_command", side_effect=self.fake_run_command
        ), patch.object(self.engine, "notify_operator", return_value=None):
            self.engine.work(self.connection, limit=20)
            approval_id = self.engine.status_summary(self.connection, workflow_id=workflow_id)["approvals"][0]["id"]
            self.engine.resolve_approval(self.connection, approval_id, "approved", "", "telegram")
            self.engine.work(self.connection, limit=3)
            self.engine.enqueue_publish_bundle(self.connection, workflow_id, ["newsletter", "linkedin", "x"])
            self.engine.work(self.connection, limit=10)

        status = self.engine.status_summary(self.connection, workflow_id=workflow_id)
        self.assertEqual(status["workflow"]["status"], "published")
        self.assertEqual(status["workflow"]["stage"], "published")
        publish_lanes = {
            row["lane"]
            for row in self.connection.execute(
                "SELECT lane FROM jobs WHERE workflow_id = ? AND step_name LIKE 'publish_%'",
                (workflow_id,),
            ).fetchall()
        }
        self.assertEqual(publish_lanes, {"distribution-lane"})

    def test_workflow_blocks_without_real_launch_path(self) -> None:
        workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Launch Path Audit",
            price_usd=39,
            product_type="guide",
        )

        with patch.object(self.engine, "generate_text", side_effect=self.fake_generation), patch.object(
            self.engine, "run_command", side_effect=self.fake_run_command_manual_launch_path
        ), patch.object(self.engine, "notify_operator", return_value=None):
            results = self.engine.work(self.connection, limit=20)

        self.assertTrue(results)
        status = self.engine.status_summary(self.connection, workflow_id=workflow_id)
        # B2: First DependencyBlocked now triggers retry instead of immediate block.
        # Workflow goes to active/dependency-retry state; job is re-queued with future run_after.
        self.assertIn(status["workflow"]["status"], ("active", "blocked"))
        dep_jobs = [
            job for job in status["jobs"]
            if job["blocked_reason"] and "waitlist API missing" in job["blocked_reason"]
        ]
        self.assertTrue(dep_jobs)

    def test_sequencer_requires_sent_cold_email_before_voice(self) -> None:
        sequencer = self.load_sequencer_module()
        self.insert_workflow(
            workflow_id="wf_voice_guard",
            stage="sequence-active",
            context={
                "name": "Arjun",
                "email": "arjun@example.com",
                "phone": "+14155550123",
                "seq": {
                    "sequence_started_at": "2026-04-28T09:00:00",
                    "touch_log": [
                        {"kind": "email-cold-1", "channel": "email", "status": "queued", "sent_at": "2026-04-28T09:00:00"}
                    ],
                },
            },
        )

        row = self.connection.execute("SELECT * FROM workflows WHERE id=?", ("wf_voice_guard",)).fetchone()
        with patch.object(sequencer, "_dispatch_touch", side_effect=AssertionError("voice should be blocked before sent cold email")):
            dispatched = sequencer._process_workflow(self.connection, row)

        self.assertEqual(dispatched, 0)

    def test_sequencer_stops_at_day1_warmup_cap(self) -> None:
        sequencer = self.load_sequencer_module()
        for idx in range(6):
            self.insert_workflow(
                workflow_id=f"wf_cap_{idx}",
                stage="cold-email-pending",
                context={
                    "name": f"Lead {idx}",
                    "email": f"lead{idx}@example.com",
                    "seq": {},
                },
            )

        def fake_dispatch(conn, workflow, ctx, touch):
            seq = ctx.setdefault("seq", {})
            touch_log = seq.setdefault("touch_log", [])
            touch_log.append(
                {
                    "kind": touch["kind"],
                    "channel": touch["channel"],
                    "status": "queued",
                    "sent_at": sequencer._now_iso(),
                }
            )
            sequencer._save_context(conn, workflow["id"], ctx)
            return True

        with patch.object(sequencer, "_dispatch_touch", side_effect=fake_dispatch) as dispatch_mock:
            total = sequencer.tick(self.connection)

        self.assertEqual(total, 5)
        self.assertEqual(dispatch_mock.call_count, 5)

    def test_waitlist_launch_path_can_reach_launch_ready(self) -> None:
        os.environ["RICK_DEFAULT_WAITLIST_API"] = "https://waitlist.rick.ai/api/signup"
        self.db, self.llm, self.engine = self.load_runtime_modules()

        workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Waitlist First Offer",
            price_usd=19,
            product_type="guide",
        )

        with patch.object(self.engine, "generate_text", side_effect=self.fake_generation), patch.object(
            self.engine, "run_command", side_effect=self.fake_run_command_manual_launch_path
        ), patch.object(self.engine, "notify_operator", return_value=None):
            self.engine.work(self.connection, limit=20)

        approval_id = self.engine.status_summary(self.connection, workflow_id=workflow_id)["approvals"][0]["id"]
        with patch.object(self.engine, "notify_operator", return_value=None):
            self.engine.resolve_approval(self.connection, approval_id, "approved", "waitlist flow ok", "telegram")

        with patch.object(self.engine, "generate_text", side_effect=self.fake_generation), patch.object(
            self.engine, "run_command", side_effect=self.fake_run_command_manual_launch_path
        ), patch.object(self.engine, "notify_operator", return_value=None):
            self.engine.work(self.connection, limit=3)

        status = self.engine.status_summary(self.connection, workflow_id=workflow_id)
        self.assertEqual(status["workflow"]["status"], "launch-ready")
        self.assertEqual(status["workflow"]["stage"], "launch-ready")

    def test_post_purchase_fulfillment_records_customer_and_enrolls_sequence(self) -> None:
        source_workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Customer Success Playbook",
            price_usd=59,
            product_type="guide",
        )
        fulfillment_id = self.engine.queue_post_purchase_workflow(
            self.connection,
            source_workflow_id=source_workflow_id,
            email="buyer@example.com",
            customer_name="Buyer One",
            payment_id="pi_123",
            amount_usd=59.0,
            delivery_url="https://deliver.rick.ai/customer-success-playbook",
            source="stripe",
        )

        with patch.object(self.engine, "generate_text", side_effect=self.fake_generation), patch.object(
            self.engine, "notify_operator", return_value=None
        ):
            self.engine.work(self.connection, limit=10)

        status = self.engine.status_summary(self.connection, workflow_id=fulfillment_id)
        self.assertEqual(status["workflow"]["status"], "fulfilled")
        self.assertEqual(status["workflow"]["stage"], "fulfilled")

        customer = self.connection.execute("SELECT * FROM customers WHERE email = 'buyer@example.com'").fetchone()
        self.assertIsNotNone(customer)
        self.assertEqual(customer["source"], "stripe")

        customer_event_count = self.connection.execute(
            "SELECT COUNT(*) AS count FROM customer_events WHERE customer_id = ?",
            (customer["id"],),
        ).fetchone()["count"]
        self.assertGreaterEqual(customer_event_count, 2)

        self.assertTrue((self.data_root / "customers" / "buyer-at-example-com.md").exists())
        self.assertTrue(
            (self.data_root / "mailbox" / "outbox" / "buyer-at-example-com-customer-success-playbook-delivery.md").exists()
        )
        sequence_config_path = self.data_root / "mailbox" / "sequences" / "customer-success-playbook-post-purchase" / "sequence.json"
        self.assertTrue(sequence_config_path.exists())
        sequence_payload = json.loads(sequence_config_path.read_text(encoding="utf-8"))
        self.assertEqual(len(sequence_payload["enrollments"]), 1)

    def test_email_sequence_dispatch_creates_due_outbox_draft(self) -> None:
        source_workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Dispatchable Product",
            price_usd=29,
            product_type="guide",
        )
        self.engine.queue_post_purchase_workflow(
            self.connection,
            source_workflow_id=source_workflow_id,
            email="dispatch@example.com",
            customer_name="Dispatch User",
            payment_id="pi_dispatch",
            amount_usd=29.0,
            delivery_url="https://deliver.rick.ai/dispatchable-product",
            source="stripe",
        )

        with patch.object(self.engine, "generate_text", side_effect=self.fake_generation), patch.object(
            self.engine, "notify_operator", return_value=None
        ):
            self.engine.work(self.connection, limit=10)

        dispatcher = self.load_script_module(
            "email_sequence_dispatch",
            ROOT_DIR / "skills" / "email-automation" / "scripts" / "email-sequence-dispatch.py",
        )
        result = dispatcher.command_dispatch(dry_run=False)
        self.assertEqual(result, 0)

        outbox_dir = self.data_root / "mailbox" / "outbox" / "dispatchable-product-post-purchase"
        drafts = list(outbox_dir.glob("*.md"))
        self.assertEqual(len(drafts), 1)
        sequence_payload = json.loads(
            (self.data_root / "mailbox" / "sequences" / "dispatchable-product-post-purchase" / "sequence.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sequence_payload["enrollments"][0]["current_step"], 1)

    def test_telegram_requires_allowed_chat(self) -> None:
        response = self.engine.parse_telegram_text(self.connection, "/status", chat_id="999")
        self.assertEqual(response, "Unauthorized chat.")

        response = self.engine.parse_telegram_text(self.connection, "/status", chat_id="12345")
        self.assertIn("Workflows:", response)

    def test_install_command_wraps_install_script(self) -> None:
        completed = subprocess.CompletedProcess(
            ["bash", str(ROOT_DIR / "scripts" / "install-rick.sh"), "--help"],
            0,
            stdout="Usage: scripts/install-rick.sh [options]\n",
            stderr="",
        )
        with patch.object(self.engine.subprocess, "run", return_value=completed) as run_mock:
            response = self.engine.parse_telegram_text(self.connection, "/install --help", chat_id="12345")

        self.assertIn("Usage: scripts/install-rick.sh", response)
        run_mock.assert_called_once()
        self.assertIn("install-rick.sh", " ".join(run_mock.call_args.args[0]))

    def test_db_init_adds_lane_columns_and_scheduler_prefers_ceo_lane(self) -> None:
        workflow_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(workflows)").fetchall()
        }
        job_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        self.assertIn("lane", workflow_columns)
        self.assertIn("lane", job_columns)

        first_workflow = self.engine.create_workflow(
            self.connection,
            "info_product_launch",
            "Research Heavy Workflow",
            "info-products",
            {"product_slug": "research-heavy"},
            priority=80,
        )
        self.engine.queue_job(
            self.connection,
            first_workflow,
            "research_brief",
            1,
            "research",
            "Research brief",
            workflow_lane="product-lane",
        )

        second_workflow = self.engine.create_workflow(
            self.connection,
            "info_product_launch",
            "CEO Decision Workflow",
            "info-products",
            {"product_slug": "ceo-decision"},
            priority=80,
        )
        self.engine.queue_job(
            self.connection,
            second_workflow,
            "approval_gate",
            8,
            "review",
            "Approval gate",
            workflow_lane="product-lane",
        )
        self.connection.commit()

        next_job = self.engine.next_runnable_job(self.connection)
        self.assertIsNotNone(next_job)
        self.assertEqual(next_job["step_name"], "approval_gate")
        self.assertEqual(next_job["lane"], "ceo-lane")

if __name__ == "__main__":
    unittest.main()
