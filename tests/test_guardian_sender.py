from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
_MOD_PATH = ROOT_DIR / "scripts" / "rick-guardian.py"


def _load():
    spec = importlib.util.spec_from_file_location("rick_guardian", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GuardianSenderTests(unittest.TestCase):
    """WHY (Rule 9): on 2026-07-23 the sender watchdog spammed Vlad ~hourly with
    a misleading '🚨 Email queue STUCK: 2 pending' while (a) the real backlog was
    12 drafts it couldn't see (it globbed only *.md), and (b) the queue wasn't
    stuck at all — a reputation circuit-breaker was deliberately holding cold
    mail. These tests lock in the three fixes: dedup stability, .json awareness,
    and channel/status exclusions."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load()

    def test_age_token_dedup_is_stable(self) -> None:
        # The whole spam bug: '50h'→'51h' each hour minted a new hash. They must
        # now normalize to the SAME hash so the alert fires at most once/window.
        h = self.mod._dedup_hash
        self.assertEqual(h("oldest 50h old", "guardian:sender_stuck"),
                         h("oldest 51h old", "guardian:sender_stuck"))
        self.assertEqual(h("held, oldest 3.5h.", "k"), h("held, oldest 99h.", "k"))

    def _write(self, name: str, payload) -> Path:
        self.mod.OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        p = self.mod.OUTBOX_DIR / name
        p.write_text(json.dumps(payload) if isinstance(payload, dict) else payload,
                     encoding="utf-8")
        return p

    def test_pending_json_email_draft_counts(self) -> None:
        p = self._write("founder-x.json", {"to": "a@b.com", "status": "pending", "channel": None})
        self.assertTrue(self.mod._is_pending_draft(p))

    def test_held_and_nonemail_and_undeliverable_excluded(self) -> None:
        held = self._write("f-held.json", {"to": "a@b.com", "status": "held"})
        hn = self._write("f-hn.json", {"to": None, "status": "queued", "channel": "hackernews_comment"})
        noaddr = self._write("f-noaddr.json", {"to": None, "status": "pending", "type": "hn_reply"})
        self.assertFalse(self.mod._is_pending_draft(held), "held drafts are not a stuck queue")
        self.assertFalse(self.mod._is_pending_draft(hn), "hackernews_comment is not email")
        self.assertFalse(self.mod._is_pending_draft(noaddr), "to=null can never deliver")

    def test_md_draft_still_counts(self) -> None:
        # Nurture .md drafts must keep counting (dir-based rule), so the fix
        # widens coverage rather than replacing it.
        p = self._write("nurture-step3.md", "# a markdown nurture draft")
        self.assertTrue(self.mod._is_pending_draft(p))


if __name__ == "__main__":
    unittest.main()
