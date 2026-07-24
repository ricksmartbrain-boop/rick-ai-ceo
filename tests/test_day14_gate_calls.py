from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
_MOD_PATH = ROOT_DIR / "scripts" / "day14-gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("day14_gate", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Day14GateCallCountTests(unittest.TestCase):
    """WHY (Rule 9): 4 days before the 2026-07-27 keep/kill decision the gate
    read GREEN off 4 'booked calls' that were all noise — a Stripe notification,
    a test @example.com address, and westcoscia counted twice — while 0 concierge
    touches had actually been sent. A revenue decision must not run on fiction.
    These tests keep the call count honest: drills, automated senders, and
    duplicate leads never inflate it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load()

    def test_automated_sender_detection(self) -> None:
        f = self.mod._is_automated_sender
        self.assertTrue(f("updates@e.stripe.com"))
        self.assertTrue(f("notifications@stripe.com"))
        self.assertTrue(f("no-reply@foo.com"))
        self.assertFalse(f("jez@jezenthomas.com"))
        self.assertFalse(f("vivid.art0944@fastmail.com"))
        self.assertFalse(f(""))

    def test_call_counts_excludes_noise_and_dedups(self) -> None:
        mod = self.mod
        rows = [
            {"intent": "CALL", "ts": "2026-07-13T17:48:57", "lead": "drill-warm@example.com"},
            {"intent": "CALL", "ts": "2026-07-20T10:38:43", "lead": "westcoscia@duck.com"},
            {"intent": "CALL", "ts": "2026-07-20T10:38:43", "lead": "updates@e.stripe.com"},
            {"intent": "CALL", "ts": "2026-07-20T17:08:48", "lead": "vivid.art0944@fastmail.com"},
            {"intent": "CALL", "ts": "2026-07-21T23:13:20", "lead": "westcoscia@duck.com"},  # dup
            {"intent": "REPLY", "ts": "2026-07-22T00:00:00", "lead": "someone@real.com"},   # not CALL
            {"intent": "CALL", "ts": "2026-07-01T00:00:00", "lead": "old@real.com"},        # pre-window
        ]
        orig = mod.read_jsonl
        mod.read_jsonl = lambda path: rows
        try:
            res = mod.call_counts()
        finally:
            mod.read_jsonl = orig
        real_leads = sorted(lead for _, lead, _ in res["real"])
        # example.com (drill) + stripe bot (automated) + dup westcoscia + non-CALL
        # + pre-window all excluded → exactly 2 distinct real human call-intent leads
        self.assertEqual(real_leads, ["vivid.art0944@fastmail.com", "westcoscia@duck.com"])
        self.assertEqual(len(res["real"]), 2)


if __name__ == "__main__":
    unittest.main()
