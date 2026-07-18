from __future__ import annotations

import importlib
import importlib.util
import json
from datetime import datetime, timedelta
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
                # Dead binary => dispatch_openclaw fails instantly and work()
                # falls back to inline handlers. Without this, any test that
                # drives work() on a machine with a live gateway delegates its
                # jobs to REAL subagents (iris/remy) — burning tokens, writing
                # junk into the live vault, and making assertions on tempdir
                # state nondeterministic (observed 2026-07-16: sequence-enroll
                # dedup test spawned two live iris runs per invocation).
                "RICK_OPENCLAW_BIN": "/usr/bin/false",
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

    def test_final_step_approval_closes_workflow(self) -> None:
        # WHY: on 2026-07-16 three LinguaLive churn-save workflows sat at
        # active/approval-cleared for 7.5h after their final-step approvals
        # were approved — nothing re-enters a workflow whose jobs are all done,
        # so only the ghost reaper ever finalized them. Approval resolution on
        # the last step must close the workflow itself; a mid-step approval
        # must still leave it active for the queued next step.
        ts = "2026-07-16T09:00:00"

        def make_blocked(workflow_id: str, step_name: str, step_index: int) -> str:
            job_id = f"job_{workflow_id}"
            approval_id = f"apr_{workflow_id}"
            self.connection.execute(
                """
                INSERT INTO workflows
                  (id, kind, title, slug, project, status, stage, priority, owner, lane,
                   telegram_target, openclaw_session_key, context_json, created_at, updated_at,
                   started_at, finished_at)
                VALUES (?, 'deal_close', ?, ?, 'deals', 'blocked', 'awaiting-approval', 15, 'rick',
                        'customer-lane', '', '', '{}', ?, ?, NULL, NULL)
                """,
                (workflow_id, f"Close deal: {workflow_id}", workflow_id.replace("_", "-"), ts, ts),
            )
            self.connection.execute(
                """
                INSERT INTO jobs
                  (id, workflow_id, step_name, step_index, status, title, route, lane,
                   payload_json, approval_id, created_at, updated_at, run_after)
                VALUES (?, ?, ?, ?, 'blocked', ?, 'strategy', 'customer-lane', '{}', ?, ?, ?, ?)
                """,
                (job_id, workflow_id, step_name, step_index, step_name, approval_id, ts, ts, ts),
            )
            self.connection.execute(
                """
                INSERT INTO approvals
                  (id, workflow_id, job_id, status, area, request_text, impact_text,
                   policy_basis, requested_by, created_at)
                VALUES (?, ?, ?, 'open', 'sales', ?, '', '', 'rick', ?)
                """,
                (approval_id, workflow_id, job_id, f"[DRILL] send {step_name}", ts),
            )
            self.connection.commit()
            return approval_id

        # Final step (close_or_escalate is last in DEAL_CLOSE_STEPS) → terminal.
        approval_final = make_blocked("wf_drill_final", "close_or_escalate", 5)
        with patch.object(self.engine, "notify_operator", return_value=None):
            self.engine.resolve_approval(self.connection, approval_final, "approved", "", "telegram")
        row = self.connection.execute(
            "SELECT status, stage, finished_at FROM workflows WHERE id = 'wf_drill_final'"
        ).fetchone()
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["stage"], "completed")
        self.assertIsNotNone(row["finished_at"])

        # Mid step (gate pattern: action lives in the NEXT step) → next step
        # queued, workflow stays open.
        approval_mid = make_blocked("wf_drill_mid", "followup_sequence", 4)
        with patch.object(self.engine, "notify_operator", return_value=None):
            self.engine.resolve_approval(self.connection, approval_mid, "approved", "", "telegram")
        row = self.connection.execute(
            "SELECT status, stage, finished_at FROM workflows WHERE id = 'wf_drill_mid'"
        ).fetchone()
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["stage"], "approval-cleared")
        self.assertIsNone(row["finished_at"])
        queued = self.connection.execute(
            "SELECT step_name FROM jobs WHERE workflow_id = 'wf_drill_mid' AND status = 'queued'"
        ).fetchall()
        self.assertEqual([r["step_name"] for r in queued], ["close_or_escalate"])
        self.assertEqual(
            self.connection.execute(
                "SELECT status FROM jobs WHERE id = 'job_wf_drill_mid'"
            ).fetchone()["status"],
            "done",
        )

    def test_approved_self_gated_step_requeues_same_job(self) -> None:
        # pitch_send raises ApprovalRequired INSIDE the step that performs the
        # send. Marking it done on approval would skip the send entirely —
        # Vlad approves a $499+/$2,500 pitch and nothing ever reaches the
        # prospect (silent no-op, found 2026-07-17). Approval must re-run the
        # SAME job; the handler's granted-approval guard lets the re-run pass
        # its gate. If this test fails, approved high-value pitches are
        # silently evaporating again.
        ts = "2026-07-17T09:00:00"
        self.connection.execute(
            """
            INSERT INTO workflows
              (id, kind, title, slug, project, status, stage, priority, owner, lane,
               telegram_target, openclaw_session_key, context_json, created_at, updated_at,
               started_at, finished_at)
            VALUES ('wf_selfgate', 'deal_close', 'Close deal: selfgate', 'wf-selfgate', 'deals',
                    'blocked', 'awaiting-approval', 15, 'rick', 'customer-lane', '', '', '{}',
                    ?, ?, NULL, NULL)
            """,
            (ts, ts),
        )
        self.connection.execute(
            """
            INSERT INTO jobs
              (id, workflow_id, step_name, step_index, status, title, route, lane,
               payload_json, approval_id, created_at, updated_at, run_after)
            VALUES ('job_selfgate', 'wf_selfgate', 'pitch_send', 3, 'blocked', 'pitch_send',
                    'writing', 'customer-lane', '{}', 'apr_selfgate', ?, ?, ?)
            """,
            (ts, ts, ts),
        )
        self.connection.execute(
            """
            INSERT INTO approvals
              (id, workflow_id, job_id, status, area, request_text, impact_text,
               policy_basis, requested_by, created_at)
            VALUES ('apr_selfgate', 'wf_selfgate', 'job_selfgate', 'open', 'irreversible-brand',
                    'Send $2500 pitch to lead', '', '', 'rick', ?)
            """,
            (ts,),
        )
        self.connection.commit()
        self.assertIn("pitch_send", self.engine.SELF_GATED_STEPS)
        with patch.object(self.engine, "notify_operator", return_value=None):
            self.engine.resolve_approval(self.connection, "apr_selfgate", "approved", "", "telegram")
        job = self.connection.execute(
            "SELECT status FROM jobs WHERE id = 'job_selfgate'"
        ).fetchone()
        self.assertEqual(job["status"], "queued")
        wf = self.connection.execute(
            "SELECT status, stage, finished_at FROM workflows WHERE id = 'wf_selfgate'"
        ).fetchone()
        self.assertEqual(wf["status"], "active")
        self.assertEqual(wf["stage"], "approval-cleared")
        self.assertIsNone(wf["finished_at"])
        # No NEXT step was queued — the re-run of pitch_send itself advances
        # the workflow when it completes.
        others = self.connection.execute(
            "SELECT COUNT(*) AS c FROM jobs WHERE workflow_id = 'wf_selfgate' AND id != 'job_selfgate'"
        ).fetchone()
        self.assertEqual(others["c"], 0)
        # The approval row is now 'approved' — exactly what the handler's
        # guard reads to pass its gate on the re-run.
        apr = self.connection.execute(
            "SELECT status FROM approvals WHERE id = 'apr_selfgate'"
        ).fetchone()
        self.assertEqual(apr["status"], "approved")

    def test_publish_bundle_marks_workflow_published(self) -> None:
        workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Agent Launch Manual",
            price_usd=49,
            product_type="guide",
        )

        # Publish handlers gate on channel credentials before the (mocked)
        # run_command send. Dummy creds keep this test hermetic — without them
        # it only passed in shells that happened to source rick.env.
        dummy_creds = {
            "RESEND_API_KEY": "test-dummy",
            "LINKEDIN_ACCESS_TOKEN": "test-dummy",
            "LINKEDIN_PERSON_URN": "urn:li:person:test",
            "X_API_KEY": "test-dummy",
            "X_API_SECRET": "test-dummy",
            "X_ACCESS_TOKEN": "test-dummy",
            "X_ACCESS_SECRET": "test-dummy",
        }
        with patch.dict(os.environ, dummy_creds), patch.object(
            self.engine, "generate_text", side_effect=self.fake_generation
        ), patch.object(
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
        # 2026-07-13 fulfillment fix: delivery drafts are .json (the gated outbox
        # sender consumes ONLY .json; a bare .md sat unsendable forever — the
        # Simone bug). The draft must be pending so the gate decides the send.
        delivery_json = self.data_root / "mailbox" / "outbox" / "buyer-at-example-com-customer-success-playbook-delivery.json"
        self.assertTrue(delivery_json.exists())
        delivery_msg = json.loads(delivery_json.read_text(encoding="utf-8"))
        self.assertEqual(delivery_msg.get("status"), "pending")
        self.assertEqual(delivery_msg.get("to"), "buyer@example.com")
        sequence_config_path = self.data_root / "mailbox" / "sequences" / "customer-success-playbook-post-purchase" / "sequence.json"
        self.assertTrue(sequence_config_path.exists())
        sequence_payload = json.loads(sequence_config_path.read_text(encoding="utf-8"))
        self.assertEqual(len(sequence_payload["enrollments"]), 1)

    def test_post_purchase_arc_extends_to_day25_for_subscriptions_only(self) -> None:
        # Churn brief 2026-07-16: every voluntary cancel happened by day 23
        # while the sequence went silent after day 7. Subscriptions must get
        # a day-14 nudge and a day-25 pre-renewal notice; one-time products
        # must NOT get the renewal step (a "your renewal is coming" email to
        # a one-time buyer would be false).
        subscription = {
            "title": "LinguaLive Subscription",
            "slug": "lingualive-subscription",
            "project": "rick-v6",
            "context_json": "{}",
        }
        config_path, _ = self.engine.ensure_post_purchase_sequence(
            source_workflow=subscription,
            customer_email="sub@example.com",
            customer_name="Sub Buyer",
            delivery_url="https://www.lingualive.ai",
        )
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [(step["step"], step["delay_days"]) for step in payload["steps"]],
            [(1, 0), (2, 2), (3, 7), (4, 14), (5, 25)],
        )
        renewal_template = (config_path.parent / "renewal-5-notice.md").read_text(encoding="utf-8")
        self.assertIn("{{renewal_date}}", renewal_template)
        self.assertIn("reply", renewal_template.lower())  # reply-to-cancel courtesy line
        # Headers must stay plain ASCII: a unicode subject header bounced a
        # real access email (duck.com 550 non-RFC2047, 2026-07-13).
        self.assertTrue(renewal_template.isascii())
        self.assertTrue(renewal_template.startswith("---\nsubject: "))

        enrollment = self.engine.enroll_post_purchase_sequence(
            sequence_config_path=config_path,
            email="sub@example.com",
            customer_name="Sub Buyer",
            delivery_url="https://www.lingualive.ai",
            product_name="LinguaLive Subscription",
            workflow_id="wf_sub_test",
        )
        self.assertRegex(enrollment.get("renewal_date", ""), r"^\d{4}-\d{2}-\d{2}$")

        one_time = {
            "title": "Customer Success Playbook",
            "slug": "customer-success-playbook",
            "project": "rick-v6",
            "context_json": "{}",
        }
        config_path_ot, _ = self.engine.ensure_post_purchase_sequence(
            source_workflow=one_time,
            customer_email="buyer@example.com",
            customer_name="Buyer",
            delivery_url="https://deliver.rick.ai/customer-success-playbook",
        )
        payload_ot = json.loads(config_path_ot.read_text(encoding="utf-8"))
        self.assertEqual(len(payload_ot["steps"]), 4)
        self.assertFalse((config_path_ot.parent / "renewal-5-notice.md").exists())
        enrollment_ot = self.engine.enroll_post_purchase_sequence(
            sequence_config_path=config_path_ot,
            email="buyer@example.com",
            customer_name="Buyer",
            delivery_url="https://deliver.rick.ai/customer-success-playbook",
            product_name="Customer Success Playbook",
            workflow_id="wf_ot_test",
        )
        self.assertNotIn("renewal_date", enrollment_ot)

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
        seq_path = self.data_root / "mailbox" / "sequences" / "dispatchable-product-post-purchase" / "sequence.json"

        # Fresh enrollment: step 1 is born satisfied (the delivery_email job
        # already sent access — dispatching step 1 would duplicate it, the
        # 2026-07-18 vojta near-dup), so dispatch must find NOTHING due today.
        result = dispatcher.command_dispatch(dry_run=False)
        self.assertEqual(result, 0)
        outbox_dir = self.data_root / "mailbox" / "outbox" / "dispatchable-product-post-purchase"
        self.assertEqual(list(outbox_dir.glob("*.md")), [])
        sequence_payload = json.loads(seq_path.read_text(encoding="utf-8"))
        self.assertEqual(sequence_payload["enrollments"][0]["sent_steps"], [1])

        # Age the enrollment 3 days: step 2 (delay 2d) becomes the FIRST step
        # the dispatcher ever renders for a buyer.
        aged = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
        sequence_payload["enrollments"][0]["enrolled_at"] = aged
        sequence_payload["enrollments"][0]["last_sent_at"] = aged
        seq_path.write_text(json.dumps(sequence_payload, indent=2) + "\n", encoding="utf-8")

        result = dispatcher.command_dispatch(dry_run=False)
        self.assertEqual(result, 0)
        drafts = list(outbox_dir.glob("*.md"))
        self.assertEqual(len(drafts), 1)
        sequence_payload = json.loads(seq_path.read_text(encoding="utf-8"))
        self.assertEqual(sequence_payload["enrollments"][0]["current_step"], 2)

    def test_dispatch_withholds_renewal_notice_from_canceled_customers(self) -> None:
        # WHY: stripe-poll flips customers.status on cancel but nothing closes
        # the JSON sequence enrollment, and every voluntary cancel to date
        # happened by day 23 — so without a draft-time guard the day-25
        # "your subscription renews" notice reaches exactly the customers it
        # is false for (renewal, continued access, reply-to-cancel). The
        # renewal-class step must be withheld and the moot enrollment closed;
        # the same step must still draft for active subscribers.
        subscription = {
            "title": "LinguaLive Subscription",
            "slug": "lingualive-subscription",
            "project": "rick-v6",
            "context_json": "{}",
        }
        config_path, _ = self.engine.ensure_post_purchase_sequence(
            source_workflow=subscription,
            customer_email="canceled@example.com",
            customer_name="Gone Buyer",
            delivery_url="https://www.lingualive.ai",
        )
        for email in ("canceled@example.com", "active@example.com"):
            self.engine.enroll_post_purchase_sequence(
                sequence_config_path=config_path,
                email=email,
                customer_name="Buyer",
                delivery_url="https://www.lingualive.ai",
                product_name="LinguaLive Subscription",
                workflow_id="wf_renewal_guard",
            )
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        for enrollment in payload["enrollments"]:
            enrollment["enrolled_at"] = "2026-06-01T09:00:00"  # day 25 long past
            enrollment["current_step"] = 4
            enrollment["sent_steps"] = [1, 2, 3, 4]
        config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        now = self.engine.now_iso()
        for customer_id, email, status in (
            ("cust_gone", "canceled@example.com", "canceled"),
            ("cust_live", "active@example.com", "active"),
        ):
            self.connection.execute(
                "INSERT INTO customers (id, email, name, source, status, tags_json, metadata_json,"
                " created_at, updated_at, last_seen_at) VALUES (?, ?, 'Buyer', 'stripe', ?, '[]', '{}', ?, ?, ?)",
                (customer_id, email, status, now, now, now),
            )
        self.connection.commit()

        dispatcher = self.load_script_module(
            "email_sequence_dispatch_renewal_guard",
            ROOT_DIR / "skills" / "email-automation" / "scripts" / "email-sequence-dispatch.py",
        )
        events = dispatcher.dispatch_sequence(config_path, current_time=dispatcher.now())
        by_email = {event["email"]: event for event in events}
        self.assertEqual(by_email["canceled@example.com"]["status"], "renewal-withheld")
        self.assertEqual(by_email["canceled@example.com"]["reason"], "customer-canceled")
        self.assertEqual(by_email["active@example.com"]["status"], "drafted")

        refreshed = json.loads(config_path.read_text(encoding="utf-8"))
        by_enrollment = {e["email"]: e for e in refreshed["enrollments"]}
        self.assertEqual(by_enrollment["canceled@example.com"]["status"], "cancelled")
        self.assertNotIn(5, by_enrollment["canceled@example.com"]["sent_steps"])
        self.assertEqual(by_enrollment["active@example.com"]["status"], "completed")
        self.assertIn(5, by_enrollment["active@example.com"]["sent_steps"])
        draft_files = list(
            (self.data_root / "mailbox" / "outbox" / "lingualive-subscription-post-purchase").glob("*step5.md")
        )
        self.assertEqual(len(draft_files), 1)
        self.assertIn("active-at-example-com", draft_files[0].name)

    def test_sequence_enroll_empty_slug_fails_loud(self) -> None:
        # WHY: a hardcoded "" slug named a junk '-post-purchase' sequence dir
        # and double-enrolled a real customer (justynovo, 2026-07-16). An
        # empty product name must raise, never mint a '-prefixed' dir.
        self.insert_workflow(
            workflow_id="wf_seq_empty_slug",
            stage="delivery-email-ready",
            context={
                "customer_email": "buyer@example.com",
                "delivery_url": "https://deliver.rick.ai/some-product",
                "source_workflow_title": "",
                "product_name": "",
            },
        )
        row = self.connection.execute("SELECT * FROM workflows WHERE id = 'wf_seq_empty_slug'").fetchone()
        with self.assertRaisesRegex(self.engine.RuntimeErrorBase, "product name is required"):
            self.engine.handle_sequence_enroll(self.connection, row, None)

        with self.assertRaisesRegex(self.engine.RuntimeErrorBase, "empty slug"):
            self.engine.ensure_post_purchase_sequence(
                source_workflow={"title": "Some Product", "slug": "", "id": None, "context_json": "{}"},
                customer_email="buyer@example.com",
                customer_name="Buyer",
                delivery_url="https://deliver.rick.ai/some-product",
            )
        self.assertFalse((self.data_root / "mailbox" / "sequences" / "-post-purchase").exists())

    def test_sequence_enroll_second_enrollment_noops_with_log(self) -> None:
        # WHY: justynovo (2026-07-16) got TWO sequence instances for one
        # purchase — the second enrollment must no-op with a ledger line and
        # customer event, not create another dir that double-sends steps.
        source_workflow_id = self.engine.queue_info_product_workflow(
            self.connection,
            idea="Customer Success Playbook",
            price_usd=59,
            product_type="guide",
        )
        self.engine.queue_post_purchase_workflow(
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

        sequences_dir = self.data_root / "mailbox" / "sequences"
        self.assertEqual(len(list(sequences_dir.glob("*/sequence.json"))), 1)

        # Second enrollment for the same buyer + product (poll-sourced shape).
        self.insert_workflow(
            workflow_id="wf_seq_dup",
            stage="delivery-email-ready",
            context={
                "customer_email": "buyer@example.com",
                "delivery_url": "https://deliver.rick.ai/customer-success-playbook",
                "source_workflow_title": "Customer Success Playbook",
                "product_name": "Customer Success Playbook",
            },
        )
        row = self.connection.execute("SELECT * FROM workflows WHERE id = 'wf_seq_dup'").fetchone()
        outcome = self.engine.handle_sequence_enroll(self.connection, row, None)
        self.assertIn("duplicate enrollment skipped", outcome.summary)

        payload = json.loads(
            (sequences_dir / "customer-success-playbook-post-purchase" / "sequence.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(payload["enrollments"]), 1)
        self.assertEqual(len(list(sequences_dir.glob("*/sequence.json"))), 1)

        ledger_lines = (self.data_root / "operations" / "execution-ledger.jsonl").read_text(encoding="utf-8").splitlines()
        dedup_rows = [json.loads(line) for line in ledger_lines if json.loads(line).get("kind") == "sequence-enroll-dedup"]
        self.assertEqual(len(dedup_rows), 1)

        customer = self.connection.execute("SELECT id FROM customers WHERE email = 'buyer@example.com'").fetchone()
        event_count = self.connection.execute(
            "SELECT COUNT(*) AS count FROM customer_events WHERE customer_id = ? AND event_type = 'sequence_enroll_deduped'",
            (customer["id"],),
        ).fetchone()["count"]
        self.assertEqual(event_count, 1)

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
