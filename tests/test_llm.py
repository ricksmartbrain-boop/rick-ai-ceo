from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class LlmRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.env_backup = os.environ.copy()
        os.environ.update(
            {
                "RICK_DATA_ROOT": self.tempdir.name,
                "RICK_LLM_USAGE_LOG_FILE": str(Path(self.tempdir.name) / "operations" / "llm-usage.jsonl"),
                "RICK_MODEL_PRICING_FILE": str(Path(self.tempdir.name) / "config" / "model-pricing.json"),
                "RICK_MODEL_OPENAI_STRATEGIC": "gpt-5.4",
                "RICK_MODEL_OPENAI_STRATEGIC_PRO": "gpt-5.4-pro",
                "RICK_MODEL_OPENAI_CODING": "gpt-5.4-pro",
                "RICK_MODEL_ANTHROPIC_STRATEGIC": "claude-opus-4-6",
                "RICK_MODEL_ANTHROPIC_WORKHORSE": "claude-sonnet-4-6",
                "RICK_MODEL_GOOGLE_WORKHORSE": "gemini-3.1-pro-preview",
                "RICK_MODEL_GOOGLE_BUDGET": "gemini-3.1-flash-lite-preview",
                "RICK_MODEL_XAI_RESEARCH": "grok-4-latest",
                "RICK_STRATEGY_PANEL_ENABLED": "1",
                "RICK_STRATEGY_PANEL_MODELS": "openai:gpt-5.4-pro,anthropic:claude-opus-4-6,google:gemini-3.1-pro-preview",
                "RICK_STRATEGY_PANEL_SYNTHESIS_MODEL": "openai:gpt-5.4",
            }
        )
        pricing_path = Path(os.environ["RICK_MODEL_PRICING_FILE"])
        pricing_path.parent.mkdir(parents=True, exist_ok=True)
        pricing_path.write_text(
            """{
  "providers": {
    "openai": {"input": 2.5, "output": 15.0, "reasoning": 15.0},
    "anthropic": {"input": 3.0, "output": 15.0},
    "google": {"input": 0.5, "output": 3.0, "reasoning": 3.0}
  },
  "models": {
    "gpt-5.4-pro": {"input": 30.0, "output": 180.0, "reasoning": 180.0},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0},
    "gemini-3.1-pro-preview": {"input": 2.0, "output": 12.0, "reasoning": 12.0},
    "gpt-5.4": {"input": 2.5, "output": 15.0, "reasoning": 15.0},
    "gpt-5.6-sol": {"input": 5.0, "output": 30.0, "reasoning": 30.0},
    "gpt-5.6-terra": {"input": 2.5, "output": 15.0, "reasoning": 15.0},
    "gpt-5.6-luna": {"input": 1.0, "output": 6.0, "reasoning": 6.0},
    "gpt-5.3-codex": {"input": 1.75, "output": 14.0, "reasoning": 14.0},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "grok-4-latest": {"input": 3.0, "output": 15.0, "reasoning": 15.0}
  }
}
""",
            encoding="utf-8",
        )
        import runtime.llm as llm

        self.llm = importlib.reload(llm)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env_backup)
        self.tempdir.cleanup()

    def test_strategy_route_uses_panel_and_synthesis(self) -> None:
        calls: list[tuple[str, str, bool]] = []

        def fake_candidate(route: str, prompt: str, provider: str, model: str, fallback_text: str, allow_fallback: bool):
            calls.append((provider, model, allow_fallback))
            if prompt.startswith("You are Rick's executive synthesis layer."):
                return self.llm.GenerationResult(
                    content="# Strategic Recommendation\n\nShip the info product.\n",
                    route=route,
                    model=model,
                    runner="synth-test",
                    mode="live",
                    provider=provider,
                )
            return self.llm.GenerationResult(
                content=f"## Opinion\n\n{provider}:{model}\n",
                route=route,
                model=model,
                runner="panel-test",
                mode="live",
                provider=provider,
            )

        with patch.object(self.llm, "generate_candidate", side_effect=fake_candidate):
            result = self.llm.generate_text("strategy", "Pick the best launch plan.", "fallback plan")

        self.assertEqual(result.runner, "strategy-panel")
        self.assertEqual(result.mode, "live")
        self.assertEqual(len(calls), 4)
        self.assertEqual(
            sorted((provider, model) for provider, model, _ in calls[:3]),
            sorted(
                [
                    ("openai", "gpt-5.4-pro"),
                    ("anthropic", "claude-opus-4-6"),
                    ("google", "gemini-3.1-pro-preview"),
                ]
            ),
        )
        self.assertTrue(any(note.startswith("panel_models=") for note in result.notes))

    def test_strategy_panel_falls_back_when_all_live_calls_fail(self) -> None:
        def fake_candidate(route: str, prompt: str, provider: str, model: str, fallback_text: str, allow_fallback: bool):
            return self.llm.GenerationResult(
                content="",
                route=route,
                model=model,
                runner="test",
                mode="error",
                provider=provider,
            )

        with patch.object(self.llm, "generate_candidate", side_effect=fake_candidate):
            result = self.llm.generate_text("strategy", "Pick the best launch plan.", "fallback plan")

        self.assertEqual(result.mode, "fallback")
        self.assertIn("fallback plan", result.content)

    def test_non_strategy_routes_use_single_candidate(self) -> None:
        """Analysis is a single-candidate route: the terra primary serves and no fallback rung is touched."""
        with patch.object(
            self.llm,
            "run_live_generation",
            return_value=self.llm.LiveCallResult(
                text="done",
                runner="test",
                provider="openai",
            ),
        ) as patched:
            result = self.llm.generate_text("analysis", "Summarize the vault.", "fallback")

        self.assertEqual(result.model, "gpt-5.6-terra")
        self.assertEqual(patched.call_count, 1)
        args, _kwargs = patched.call_args
        self.assertEqual(args[0], "openai")
        self.assertEqual(args[1], "gpt-5.6-terra")
        self.assertEqual(args[2], "analysis")

    def test_non_strategy_routes_walk_fallback_chain(self) -> None:
        """When the terra primary fails, analysis escalates to the sol rung and the ledger note records it."""
        with patch.object(
            self.llm,
            "run_live_generation",
            side_effect=[
                None,
                self.llm.LiveCallResult(
                    text="fallback answer",
                    runner="openai-responses",
                    provider="openai",
                ),
            ],
        ) as patched:
            result = self.llm.generate_text("analysis", "Summarize the vault.", "fallback")

        self.assertEqual(result.model, "gpt-5.6-sol")
        self.assertIn("route_fallback_used=openai:gpt-5.6-sol", result.notes)
        self.assertEqual(patched.call_count, 2)

    def test_cost_estimation_uses_model_pricing(self) -> None:
        usage = self.llm.UsageStats(input_tokens=1_000_000, output_tokens=500_000, reasoning_tokens=500_000, estimated=False)
        cost = self.llm.estimate_generation_cost("gpt-5.4-pro", "openai", usage)
        self.assertAlmostEqual(cost, 210.0)

    def test_heartbeat_route_stays_on_budget_tier(self) -> None:
        """A1: Heartbeat is high-frequency plumbing — it must default to the budget tier
        (luna) and never chain into an expensive judgment-class model."""
        self.assertEqual(self.llm.ROUTES["heartbeat"]["default"], "gpt-5.6-luna")
        self.assertEqual(self.llm.ROUTES["heartbeat"]["env"], "RICK_MODEL_OPENAI_BUDGET")
        expensive = {"gpt-5.6-sol", "claude-opus-4-8", "gpt-5.4-pro"}
        chain_models = {model for _prov, model in self.llm.ROUTE_FALLBACK_DEFAULTS["heartbeat"]}
        self.assertFalse(chain_models & expensive, f"heartbeat chain contains expensive models: {chain_models}")

    def _write_usage_row(self, bucket: str, usd: float) -> None:
        from datetime import datetime

        usage_path = Path(os.environ["RICK_LLM_USAGE_LOG_FILE"])
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        row = {"timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "bucket": bucket, "usd": usd}
        with usage_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def test_per_bucket_budget_blocks_when_exceeded(self) -> None:
        """A3: Per-bucket caps block routes whose bucket is exhausted; heartbeat is exempt
        (it is Rick's operational pulse and must never be silenced by spend)."""
        self._write_usage_row("workhorse", 0.01)
        with patch.object(self.llm, "load_route_budgets", return_value={"workhorse": {"daily_cap_usd": 0.001}}):
            allowed, reason = self.llm.check_route_budget("writing")
            self.assertFalse(allowed)
            self.assertIn("route_budget_exceeded:workhorse", reason)

            allowed_hb, _ = self.llm.check_route_budget("heartbeat")
            self.assertTrue(allowed_hb)

    def test_budget_cap_raises_on_judgment_routes(self) -> None:
        """A6/fail-loud: a capped day must ABORT judgment work (review), never hand
        back canned text that callers could mistake for a real red-team verdict."""
        with patch.object(self.llm, "check_daily_budget", return_value=(False, 15.0)):
            with self.assertRaises(self.llm.BudgetExceeded):
                self.llm.generate_text("review", "Review this plan.", "canned verdict")

    def test_budget_cap_returns_fallback_on_non_judgment_routes(self) -> None:
        """A6: non-judgment routes (writing) degrade to the caller's fallback text with
        mode='fallback' so the pipeline can skip gracefully instead of crashing."""
        with patch.object(self.llm, "check_daily_budget", return_value=(False, 15.0)):
            result = self.llm.generate_text("writing", "Draft a post.", "canned copy")
        self.assertEqual(result.mode, "fallback")
        self.assertIn("canned copy", result.content)

    def test_claude_cli_subprocess_env_strips_api_key(self) -> None:
        """The claude-cli rung bills the subscription. An exported (possibly
        credit-dead) ANTHROPIC_API_KEY must never reach the subprocess, or the
        CLI prefers it and the rung dies with the API (2026-07-16 incident)."""
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)

            class R:
                returncode = 0
                stdout = "ok"
                stderr = ""

            return R()

        os.environ["ANTHROPIC_API_KEY"] = "sk-test-dead-key"
        with patch.object(self.llm.shutil, "which", return_value="/usr/local/bin/claude"), \
             patch.object(self.llm.subprocess, "run", side_effect=fake_run):
            result = self.llm.call_claude_cli("claude-sonnet-4-6", "writing", "hi")

        self.assertIsNotNone(result)
        self.assertIn("env", captured)
        self.assertNotIn("ANTHROPIC_API_KEY", captured["env"])
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", captured["env"])

    def test_budget_cap_never_blocks_heartbeat(self) -> None:
        """A6: heartbeat is exempt from both cap layers — Rick's pulse must keep
        reporting operational truth even on a blown-budget day."""
        self._write_usage_row("heartbeat", 999.0)
        allowed, _spent = self.llm.check_daily_budget("heartbeat")
        self.assertTrue(allowed)
        allowed_route, _ = self.llm.check_route_budget("heartbeat")
        self.assertTrue(allowed_route)


if __name__ == "__main__":
    unittest.main()
