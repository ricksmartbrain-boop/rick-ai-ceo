from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
_MOD_PATH = ROOT_DIR / "skills" / "email-nurture-machine" / "scripts" / "email-send-outbox.py"


def _load():
    spec = importlib.util.spec_from_file_location("email_send_outbox", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class OutboxFreshnessTests(unittest.TestCase):
    """WHY (Rule 9): 2026-07-23, 10 cold founder drafts sat 24-49h behind a
    bounce-rate cap of 0 — every one send-approved, so the accidental cap was
    the ONLY thing stopping a stale dead-hook batch from firing the instant the
    cap lifted. The freshness gate parks stale cold drafts as held so a cap
    restore can never auto-send them. These tests keep that gate honest."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load()

    def test_predicate(self) -> None:
        now = "2026-07-23T08:30:00"
        p = self.mod._is_stale_cold
        self.assertTrue(p({"cold": True, "send_after": "2026-07-21T09:01:41"}, now))   # 49h
        self.assertFalse(p({"cold": True, "send_after": "2026-07-23T07:01:00"}, now))  # fresh
        self.assertFalse(p({"cold": False, "send_after": "2026-07-01T00:00:00"}, now)) # not cold
        self.assertFalse(p({"cold": True, "send_after": ""}, now))                     # no time

    def test_process_outbox_parks_stale_cold(self) -> None:
        mod = self.mod
        with tempfile.TemporaryDirectory() as td:
            outbox = Path(td) / "outbox"
            outbox.mkdir(parents=True)
            (Path(td) / "sent").mkdir()
            stale_after = (datetime.now() - timedelta(hours=72)).isoformat(timespec="seconds")
            draft = outbox / "founder-stale.json"
            draft.write_text(json.dumps({
                "to": "a@b.com", "status": "pending", "cold": True,
                "send_after": stale_after, "body_markdown": "Subject: hi\n\nbody",
            }), encoding="utf-8")

            orig_outbox, orig_sent = mod.OUTBOX_DIR, mod.SENT_DIR
            orig_block = mod.email_channel_block_reason
            mod.OUTBOX_DIR, mod.SENT_DIR = outbox, Path(td) / "sent"
            mod.email_channel_block_reason = lambda transactional=False: None  # no runtime dep
            try:
                mod.process_outbox(dry_run=False)
            finally:
                mod.OUTBOX_DIR, mod.SENT_DIR = orig_outbox, orig_sent
                mod.email_channel_block_reason = orig_block

            after = json.loads(draft.read_text(encoding="utf-8"))
            self.assertEqual(after["status"], "held", "stale cold draft must be parked, not sent")
            self.assertIn("stale-cold", after.get("held_reason", ""))


if __name__ == "__main__":
    unittest.main()
