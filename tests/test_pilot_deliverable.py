from __future__ import annotations

import importlib.util
import types
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
_MOD_PATH = ROOT_DIR / "scripts" / "pilot-deliverable.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pilot_deliverable", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # runs founder-sourcer env setup (isolated by conftest)
    return mod


def _fake_result(content: str, mode: str = "live", model: str = "gpt-5.6-terra"):
    return types.SimpleNamespace(content=content, mode=mode, model=model)


class PilotDeliverableResilienceTests(unittest.TestCase):
    """WHY (Rule 9): pilot-deliverable used a direct api.anthropic.com call. With
    the Anthropic API credit-dead, that returned nothing and the script SILENTLY
    shipped a generic hardcoded ICP + 10 unrelated prospects as a paid customer's
    'personalized' Day-1 deliverable. These tests lock in the two guarantees of
    the fix: (1) generation routes through the resilient chain, and (2) a
    degraded ICP is detectable so main() can refuse to ship it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()

    def test_routes_are_real(self) -> None:
        from runtime.llm import ROUTES
        self.assertIn(self.mod.ROUTE_REASON, ROUTES)
        self.assertIn(self.mod.ROUTE_WRITE, ROUTES)

    def test_llm_generate_returns_empty_on_fallback(self) -> None:
        import runtime.llm as llm
        orig = llm.generate_text
        llm.generate_text = lambda *a, **k: _fake_result(self.mod._LLM_FALLBACK, mode="fallback")
        try:
            self.assertEqual(self.mod.llm_generate("analysis", "x"), "")
        finally:
            llm.generate_text = orig

    def test_llm_generate_passes_live_text(self) -> None:
        import runtime.llm as llm
        orig = llm.generate_text
        llm.generate_text = lambda *a, **k: _fake_result('{"ok": true}')
        try:
            self.assertEqual(self.mod.llm_generate("analysis", "x"), '{"ok": true}')
        finally:
            llm.generate_text = orig

    def test_icp_marked_degraded_when_llm_dead(self) -> None:
        import runtime.llm as llm
        orig = llm.generate_text
        llm.generate_text = lambda *a, **k: _fake_result("", mode="fallback")
        try:
            icp = self.mod.infer_icp(
                {"company_url": "https://example.com", "bottleneck": "x"}, {})
            self.assertTrue(icp.get("_degraded"),
                            "generic-placeholder ICP must be flagged so main() can refuse to ship it")
        finally:
            llm.generate_text = orig

    def test_build_html_survives_missing_email(self) -> None:
        # A pilot intake without 'email' used to KeyError at the FINAL render —
        # after ~11 LLM calls were already spent. email is display-only; render
        # must degrade, not crash.
        import runtime.llm as llm
        orig = llm.generate_text
        llm.generate_text = lambda *a, **k: _fake_result("", mode="fallback")
        try:
            intake = {"company_url": "https://example.com", "name": "Dana", "bottleneck": "churn"}
            icp = self.mod.infer_icp(intake, {})  # full-shaped fallback dict
            emails = [{"name": "N", "company": "C", "domain": "c.com",
                       "why_fit": "f", "subject": "s", "body": "b"}]
            html = self.mod.build_html(intake, icp, emails)
            self.assertIn("(no email on file)", html)
            self.assertIn("Dana", html)
        finally:
            llm.generate_text = orig

    def test_icp_not_degraded_on_real_response(self) -> None:
        import runtime.llm as llm
        real = ('{"company_one_liner":"x","icp_segment":"s","icp_persona":"p",'
                '"prospect_signals":["a"],"outbound_angle":"o","sample_prospects":'
                '[{"name":"N","company":"C","domain":"c.com","why_fit":"f"}]}')
        orig = llm.generate_text
        llm.generate_text = lambda *a, **k: _fake_result(real)
        try:
            icp = self.mod.infer_icp(
                {"company_url": "https://example.com", "bottleneck": "x"}, {})
            self.assertNotIn("_degraded", icp)
            self.assertEqual(icp["company_one_liner"], "x")
        finally:
            llm.generate_text = orig


if __name__ == "__main__":
    unittest.main()
