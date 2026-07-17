from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class OpenClawPreSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.tempdir.name)
        self.data_root = self.temp_path / "rick-vault"
        self.workspace_root = self.temp_path / "workspace"
        self.env_file = self.temp_path / "rick.env"
        self.env_file.write_text("", encoding="utf-8")
        self.base_env = os.environ.copy()
        self.base_env.update(
            {
                "RICK_ENV_FILE": str(self.env_file),
                "RICK_WORKSPACE_ROOT": str(self.workspace_root),
                "RICK_OPENCLAW_HOME": str(self.workspace_root),
                "RICK_OPENCLAW_MAIN_AGENT_ID": "rick",
                "RICK_OPENCLAW_SESSION_POLICY_FILE": str(self.workspace_root / "config" / "openclaw-session-policy.json"),
                "RICK_OPENCLAW_AGENT_BLUEPRINT_FILE": str(self.workspace_root / "config" / "openclaw-agent-blueprint.json"),
                "RICK_OPENCLAW_MEMORY_FLUSH_PROMPT_FILE": str(ROOT_DIR / "templates" / "openclaw" / "memory-flush.prompt.md"),
                "RICK_OPENCLAW_SECURE_DM_MODE": "prepared",
                "RICK_DATA_ROOT": str(self.data_root),
                "RICK_RUNTIME_DB_FILE": str(self.data_root / "runtime" / "rick-runtime.db"),
                "RICK_EXECUTION_LEDGER_FILE": str(self.data_root / "operations" / "execution-ledger.jsonl"),
                "RICK_LLM_USAGE_LOG_FILE": str(self.data_root / "operations" / "llm-usage.jsonl"),
                "RICK_MEMORY_INDEX_FILE": str(self.data_root / "control" / "memory-index.json"),
                "RICK_MEMORY_OVERVIEW_FILE": str(self.data_root / "dashboards" / "memory-overview.md"),
                "RICK_PORTFOLIO_SCORECARDS_FILE": str(self.workspace_root / "config" / "portfolio-scorecards.json"),
                "RICK_STRIPE_ACCOUNTS_FILE": str(self.workspace_root / "config" / "stripe-accounts.json"),
                "RICK_SITES_FILE": str(self.workspace_root / "config" / "sites.json"),
                "RICK_APPROVAL_POLICY_FILE": str(self.workspace_root / "config" / "approval-policy.json"),
                "RICK_LANE_POLICY_FILE": str(self.workspace_root / "config" / "lane-policy.json"),
                "RICK_WATCHDOG_PROCESSES_FILE": str(self.workspace_root / "config" / "watchdog-processes.json"),
                "RICK_TELEGRAM_TOPICS_FILE": str(self.workspace_root / "config" / "telegram-topics.json"),
                "RICK_TOKEN_BUDGET_FILE": str(self.workspace_root / "config" / "token-budgets.json"),
                "RICK_MODEL_PRICING_FILE": str(self.workspace_root / "config" / "model-pricing.json"),
                "RICK_PORTFOLIO_FILE": str(self.workspace_root / "config" / "portfolio.json"),
                "RICK_TMUX_SOCKET_PATH": str(self.temp_path / "tmux.sock"),
                "RICK_XPOST_BIN": str(ROOT_DIR / "bin" / "xpost"),
                "RICK_PRIMARY_DOMAIN": "https://rick.example.com",
                "RICK_X_HANDLE": "@rickbuilds",
                "RICK_NEWSLETTER_PLATFORM": "substack",
                "RICK_TELEGRAM_BOT_TOKEN": "telegram-test-token",
                "RICK_TELEGRAM_ALLOWED_CHAT_ID": "-1009000",
                "RICK_TELEGRAM_THREAD_MODE": "hybrid",
                "RICK_TELEGRAM_FORUM_CHAT_ID": "-1009000",
                "RICK_MODEL_OPENAI_STRATEGIC": "gpt-5.4",
                "RICK_MODEL_OPENAI_STRATEGIC_PRO": "gpt-5.4-pro",
                "RICK_MODEL_OPENAI_CODING": "gpt-5.4-pro",
                "RICK_MODEL_ANTHROPIC_STRATEGIC": "claude-opus-4-6",
                "RICK_MODEL_ANTHROPIC_WORKHORSE": "claude-sonnet-4-6",
                "RICK_MODEL_GOOGLE_WORKHORSE": "gemini-3.1-pro-preview",
                "RICK_MODEL_GOOGLE_BUDGET": "gemini-3.1-flash-lite-preview",
                "RICK_MODEL_XAI_RESEARCH": "grok-4-latest",
                "OPENAI_API_KEY": "test-openai",
                "ANTHROPIC_API_KEY": "test-anthropic",
                "GOOGLE_API_KEY": "test-google",
                "XAI_API_KEY": "test-xai",
            }
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_script(self, relative_path: str, *args: str, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["bash", str(ROOT_DIR / relative_path), *args],
            capture_output=True,
            text=True,
            env=self.base_env,
            cwd=str(ROOT_DIR),
            check=False,
        )
        if expect_success and result.returncode != 0:
            raise AssertionError(f"{relative_path} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        return result

    def test_bootstrap_copies_openclaw_profile_assets(self) -> None:
        self.run_script("scripts/bootstrap.sh")

        self.assertTrue((self.workspace_root / "config" / "openclaw-session-policy.json").exists())
        self.assertTrue((self.workspace_root / "config" / "openclaw-agent-blueprint.json").exists())
        self.assertTrue((self.data_root / "control" / "openclaw-profile.md").exists())

        session_policy = json.loads(
            (self.workspace_root / "config" / "openclaw-session-policy.json").read_text(encoding="utf-8")
        )
        self.assertEqual(session_policy["main_agent_id"], "rick")
        self.assertEqual(session_policy["session"]["maintenance"]["mode"], "enforce")
        self.assertEqual(session_policy["session"]["dm"]["status"], "prepared")
        self.assertFalse(session_policy["session"]["dm"]["enabled"])

    def test_doctor_reports_openclaw_profile_gaps_and_clears_after_bootstrap(self) -> None:
        initial = self.run_script("scripts/doctor.sh")
        self.assertIn("RICK_OPENCLAW_SESSION_POLICY_FILE", initial.stdout)
        self.assertIn("RICK_OPENCLAW_AGENT_BLUEPRINT_FILE", initial.stdout)

        self.run_script("scripts/bootstrap.sh")
        after = self.run_script("scripts/doctor.sh")

        self.assertNotIn(
            f"RICK_OPENCLAW_SESSION_POLICY_FILE:{self.workspace_root / 'config' / 'openclaw-session-policy.json'}",
            after.stdout,
        )
        self.assertNotIn(
            f"RICK_OPENCLAW_AGENT_BLUEPRINT_FILE:{self.workspace_root / 'config' / 'openclaw-agent-blueprint.json'}",
            after.stdout,
        )
        self.assertNotIn("thread mode enabled but session policy file is missing", after.stdout)

    def test_guardrails_audit_founder_gating_reads_openclaw_allowlist(self) -> None:
        # Real Telegram gating is the openclaw.json allowlist, not legacy env vars:
        # the audit must pass on a restrictive config and fail closed otherwise.
        config_path = self.temp_path / "openclaw.json"
        self.base_env["RICK_OPENCLAW_CONFIG_FILE"] = str(config_path)

        config_path.write_text(
            json.dumps({"channels": {"telegram": {"dmPolicy": "allowlist", "allowFrom": [203132131], "groupPolicy": "allowlist"}}}),
            encoding="utf-8",
        )
        restrictive = self.run_script("scripts/guardrails-audit.sh")
        self.assertIn("| Founder control gating | pass |", restrictive.stdout)

        config_path.write_text(
            json.dumps({"channels": {"telegram": {"dmPolicy": "allowlist", "allowFrom": [], "groupPolicy": "allowlist"}}}),
            encoding="utf-8",
        )
        empty_allowlist = self.run_script("scripts/guardrails-audit.sh")
        self.assertIn("| Founder control gating | fail |", empty_allowlist.stdout)

        config_path.unlink()
        missing = self.run_script("scripts/guardrails-audit.sh")
        self.assertIn("| Founder control gating | fail |", missing.stdout)

    def test_docs_capture_single_agent_now_and_four_agent_later(self) -> None:
        readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
        setup_doc = (ROOT_DIR / "OPENCLAW_SETUP.md").read_text(encoding="utf-8")
        profile_doc = (ROOT_DIR / "OPENCLAW_PROFILE.md").read_text(encoding="utf-8")

        self.assertIn("one main OpenClaw agent (`rick`) stays active", readme)
        self.assertIn("future `rick-ceo`, `rick-builder`, `rick-distribution`, and `rick-customer-ops`", setup_doc)
        self.assertIn("Only one OpenClaw agent should be active now", profile_doc)


if __name__ == "__main__":
    unittest.main()
