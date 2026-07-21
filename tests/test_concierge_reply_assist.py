from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
_MOD_PATH = ROOT_DIR / "scripts" / "concierge-reply-assist.py"


def _load_module():
    # dash-in-name → importlib; loading runs founder-sourcer's env setup, which
    # is harmless in-process and mirrors how the script itself imports.
    spec = importlib.util.spec_from_file_location("concierge_reply_assist", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ReplyAssistGuardrailTests(unittest.TestCase):
    """WHY (Rule 9): the model drafts prospect-facing replies. A hallucinated
    price, off-terms guarantee, or a link to some invented domain would go out
    over Vlad's name to a real founder. These post-checks are the deterministic
    net under the model — if they stop flagging, a fabricated offer term ships
    silently. Each case asserts the net catches the thing it exists to catch."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()

    def test_clean_offer_terms_pass(self) -> None:
        draft = ("Happy to start the free week. Month 1 is $249, then $499/mo, "
                 "and it's 100 net-new signups/trials in 14 days or your money "
                 "back. Grab a slot: https://meetrick.ai/pilot — Vlad")
        self.assertEqual(self.mod.post_checks(draft, ("meetrick.ai",)), [])

    def test_stray_price_flagged(self) -> None:
        draft = "For you I'll do $99/mo, no problem. — Vlad"
        flags = self.mod.post_checks(draft, ("meetrick.ai",))
        self.assertTrue(any("stray price" in f and "$99" in f for f in flags), flags)

    def test_offer_prices_not_flagged(self) -> None:
        draft = "It's $249 the first month, then $499. — Vlad"
        self.assertEqual(self.mod.post_checks(draft, ("meetrick.ai",)), [])

    def test_foreign_url_flagged_prospect_domain_allowed(self) -> None:
        draft = ("Loved tutoriapp.ai. Details at https://tutoriapp.ai/pricing and "
                 "https://sketchy-affiliate.example/ref — Vlad")
        flags = self.mod.post_checks(draft, ("meetrick.ai", "tutoriapp.ai"))
        self.assertTrue(any("sketchy-affiliate.example" in f for f in flags), flags)
        self.assertFalse(any("tutoriapp.ai" in f for f in flags), flags)

    def test_off_terms_guarantee_flagged(self) -> None:
        draft = "I guarantee 500 net-new signups in 30 days. — Vlad"
        flags = self.mod.post_checks(draft, ("meetrick.ai",))
        self.assertTrue(any("guarantee count '500" in f for f in flags), flags)
        self.assertTrue(any("guarantee window '30 days" in f for f in flags), flags)

    def test_free_week_seven_days_not_flagged(self) -> None:
        # The free week is legitimately 7 days; only guarantee-claim sentences
        # get their day-counts checked, so this must NOT flag (alert quality —
        # over-flagging trains the operator to ignore flags).
        draft = ("The pilot starts with a free week — if I can't show you "
                 "something real in 7 days, we stop there. The guarantee is "
                 "100 net-new signups or trials in 14 days. — Vlad")
        self.assertEqual(self.mod.post_checks(draft, ("meetrick.ai",)), [])

    def test_needs_vlad_gap_surfaced(self) -> None:
        draft = "Great question — [NEEDS VLAD: do we have a Spanish tutor case study?] — Vlad"
        flags = self.mod.post_checks(draft, ("meetrick.ai",))
        self.assertTrue(any("NEEDS VLAD" in f for f in flags), flags)

    def test_offer_constant_carries_live_terms(self) -> None:
        # The injected offer must state the current terms; a drift here means
        # every drafted reply quotes stale pricing.
        for token in ("$249", "$499", "100 net-new", "14 days", "meetrick.ai/pilot"):
            self.assertIn(token, self.mod.OFFER)


if __name__ == "__main__":
    unittest.main()
