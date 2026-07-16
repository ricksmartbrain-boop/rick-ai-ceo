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


class Gpt56ModelRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        pricing_file = Path(self.tempdir.name) / "config" / "model-pricing.json"
        pricing_file.parent.mkdir(parents=True, exist_ok=True)
        pricing_file.write_text(
            json.dumps(
                {
                    "providers": {"openai": {"input": 2.5, "output": 15.0, "reasoning": 15.0}},
                    "models": {
                        "gpt-5.6": {"input": 5.0, "output": 30.0, "reasoning": 30.0},
                        "gpt-5.6-sol": {"input": 5.0, "output": 30.0, "reasoning": 30.0},
                        "gpt-5.6-terra": {"input": 2.5, "output": 15.0, "reasoning": 15.0},
                        "gpt-5.6-luna": {"input": 1.0, "output": 6.0, "reasoning": 6.0},
                    },
                }
            ),
            encoding="utf-8",
        )
        self.env = patch.dict(
            os.environ,
            {
                "RICK_DATA_ROOT": self.tempdir.name,
                "RICK_LLM_USAGE_LOG_FILE": str(Path(self.tempdir.name) / "operations" / "llm-usage.jsonl"),
                "RICK_MODEL_PRICING_FILE": str(pricing_file),
            },
            clear=True,
        )
        self.env.start()
        import runtime.llm as llm

        self.llm = importlib.reload(llm)

    def tearDown(self) -> None:
        self.env.stop()
        self.tempdir.cleanup()

    def test_openai_defaults_use_gpt_56_family(self) -> None:
        self.assertEqual(self.llm.ROUTES["strategy"]["default"], "gpt-5.6-sol")
        self.assertEqual(self.llm.ROUTES["coding"]["default"], "gpt-5.6-sol")
        self.assertIn(("openai", "gpt-5.6-terra"), self.llm.ROUTE_FALLBACK_DEFAULTS["coding"])
        # 2026-07-16: terra/luna were promoted from fallback rungs to route primaries.
        self.assertEqual(self.llm.ROUTES["analysis"]["default"], "gpt-5.6-terra")
        self.assertIn(("openai", "gpt-5.6-sol"), self.llm.ROUTE_FALLBACK_DEFAULTS["analysis"])
        self.assertEqual(self.llm.ROUTES["heartbeat"]["default"], "gpt-5.6-luna")
        self.assertIn(("openai", "gpt-5.6-terra"), self.llm.ROUTE_FALLBACK_DEFAULTS["heartbeat"])
        self.assertEqual(self.llm.STRATEGY_SYNTHESIS_DEFAULT[2], "openai:gpt-5.6-sol")

    def test_gpt_56_pricing_is_registered(self) -> None:
        usage = self.llm.UsageStats(input_tokens=1_000_000, output_tokens=500_000, reasoning_tokens=500_000)

        self.assertAlmostEqual(self.llm.estimate_generation_cost("gpt-5.6-sol", "openai", usage), 35.0)
        self.assertAlmostEqual(self.llm.estimate_generation_cost("gpt-5.6-terra", "openai", usage), 17.5)
        self.assertAlmostEqual(self.llm.estimate_generation_cost("gpt-5.6-luna", "openai", usage), 7.0)
        self.assertAlmostEqual(self.llm.estimate_generation_cost("gpt-5.6", "openai", usage), 35.0)


if __name__ == "__main__":
    unittest.main()
