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
    "gemini-3.1-pro-preview": {"input": 2.0, "output": 12.0, "reasoning": 12.0}
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

        self.assertEqual(result.model, "gpt-5.4")
        patched.assert_called_once_with("openai", "gpt-5.4", "analysis", "Summarize the vault.")

    def test_non_strategy_routes_walk_fallback_chain(self) -> None:
        with patch.object(
            self.llm,
            "run_live_generation",
            side_effect=[
                None,
                self.llm.LiveCallResult(
                    text="fallback answer",
                    runner="anthropic-api",
                    provider="anthropic",
                ),
            ],
        ) as patched:
            result = self.llm.generate_text("analysis", "Summarize the vault.", "fallback")

        self.assertEqual(result.model, "claude-opus-4-6")
        self.assertIn("route_fallback_used=anthropic:claude-opus-4-6", result.notes)
        self.assertEqual(patched.call_count, 2)

    def test_cost_estimation_uses_model_pricing(self) -> None:
        usage = self.llm.UsageStats(input_tokens=1_000_000, output_tokens=500_000, reasoning_tokens=500_000, estimated=False)
        cost = self.llm.estimate_generation_cost("gpt-5.4-pro", "openai", usage)
        self.assertAlmostEqual(cost, 210.0)

    def test_heartbeat_route_defaults_to_haiku(self) -> None:
        """A1: Heartbeat should use cheap model by default."""
        self.assertEqual(self.llm.ROUTES["heartbeat"]["default"], "claude-haiku-4-5-20251001")
        self.assertEqual(self.llm.ROUTES["heartbeat"]["env"], "RICK_MODEL_ANTHROPIC_CHEAP")

    def test_per_bucket_budget_blocks_when_exceeded(self) -> None:
        """A3: Per-bucket caps should block routes that exceed their bucket cap."""
        budget_path = Path(self.tempdir.name) / "config" / "token-budgets.json"
        budget_path.parent.mkdir(parents=True, exist_ok=True)
        budget_path.write_text(json.dumps({"daily_usd_caps": {"workhorse": 0.001}}), encoding="utf-8")

        self.llm = importlib.reload(self.llm)
        # Patch TOKEN_BUDGET_FILE to our test file
        with patch.object(self.llm, "TOKEN_BUDGET_FILE", budget_path):
            # Directly inject bucket spend above the cap via the cache
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            self.llm._daily_spend_cache["date"] = today
            self.llm._daily_spend_cache["total"] = 0.01
            self.llm._daily_bucket_spend_cache[today] = {"workhorse": 0.01}

            allowed, _ = self.llm.check_daily_budget("writing")
            self.assertFalse(allowed)

            # Heartbeat should still be allowed
            allowed_hb, _ = self.llm.check_daily_budget("heartbeat")
            self.assertTrue(allowed_hb)

    def test_budget_pressure_level(self) -> None:
        """A6: Budget pressure returns correct levels."""
        with patch.object(self.llm, "daily_spend_usd", return_value=10.0), \
             patch.object(self.llm, "_get_daily_cap", return_value=50.0):
            self.assertEqual(self.llm.budget_pressure_level(), "normal")

        with patch.object(self.llm, "daily_spend_usd", return_value=42.0), \
             patch.object(self.llm, "_get_daily_cap", return_value=50.0):
            self.assertEqual(self.llm.budget_pressure_level(), "tight")

        with patch.object(self.llm, "daily_spend_usd", return_value=46.0), \
             patch.object(self.llm, "_get_daily_cap", return_value=50.0):
            self.assertEqual(self.llm.budget_pressure_level(), "critical")

    def test_graceful_degradation_downgrades_on_tight_budget(self) -> None:
        """A6: Under tight budget pressure, routes should downgrade to cheapest fallback."""
        with patch.object(self.llm, "budget_pressure_level", return_value="tight"):
            provider, model = self.llm._apply_budget_degradation("writing", "anthropic", "claude-sonnet-4-6")
            # Should use the last fallback in the chain (cheapest)
            self.assertNotEqual(model, "claude-sonnet-4-6")

        with patch.object(self.llm, "budget_pressure_level", return_value="critical"):
            provider, model = self.llm._apply_budget_degradation("writing", "anthropic", "claude-sonnet-4-6")
            self.assertEqual(model, "claude-haiku-4-5-20251001")

    def test_graceful_degradation_does_not_downgrade_heartbeat(self) -> None:
        """A6: Heartbeat and cheap routes should not be downgraded even under critical pressure."""
        with patch.object(self.llm, "budget_pressure_level", return_value="critical"):
            provider, model = self.llm._apply_budget_degradation("heartbeat", "anthropic", "claude-haiku-4-5-20251001")
            self.assertEqual(model, "claude-haiku-4-5-20251001")

            provider, model = self.llm._apply_budget_degradation("cheap", "anthropic", "claude-haiku-4-5-20251001")
            self.assertEqual(model, "claude-haiku-4-5-20251001")

    def test_latency_tracked_on_live_call_result(self) -> None:
        """D1: LiveCallResult should have latency_ms field."""
        result = self.llm.LiveCallResult(text="test", runner="test", provider="test", latency_ms=123.4)
        self.assertEqual(result.latency_ms, 123.4)


if __name__ == "__main__":
    unittest.main()
