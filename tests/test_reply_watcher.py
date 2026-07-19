"""Reply alerts must reach Vlad's Telegram deterministically.

WHY: on 2026-07-16/17 the only real human reply (westcoscia) fired alerts
that returned 'sent_first' via the openclaw system-event broadcast — which
only proves the gateway ACCEPTED the event. Delivery depended on a live
agent session, the session was credits-dead, and Vlad never saw either
alert (or the two staged reply drafts). The direct Bot API path must be
primary; the agent broadcast is fallback only. If these tests fail, reply
alerts are back to maybe-delivery.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]


def _load_reply_watcher():
    spec = importlib.util.spec_from_file_location(
        "reply_watcher", ROOT_DIR / "scripts" / "reply-watcher.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FireAlertDeliveryTests(unittest.TestCase):
    CTX = {
        "email": "prospect@example.test",
        "name": "Prospect",
        "label": "sales_inquiry",
        "triage_id": "t123",
        "thread_id": "",
    }
    ROW = {"subject": "Re: your note", "body": "I'm interested — what's the price?"}

    def test_direct_telegram_is_primary_and_fallback_untouched(self) -> None:
        rw = _load_reply_watcher()
        import runtime.engine as engine

        with patch.object(engine, "send_telegram_message", return_value=4242) as direct, patch.object(
            engine, "notify_operator_deduped"
        ) as fallback:
            result = rw._fire_alert(None, self.CTX, self.ROW, "", dry_run=False, verbose=False)
        self.assertEqual(result, "sent_direct:4242")
        fallback.assert_not_called()
        # Explicit routing: the proven team-chat + ops-alerts thread, never
        # the implicit default (RICK_TELEGRAM_DEFAULT_CHAT_ID is unset in prod).
        kwargs = direct.call_args.kwargs
        self.assertTrue(kwargs["chat_id"])
        self.assertIsNotNone(kwargs["thread_id"])

    def test_direct_failure_falls_back_to_agent_broadcast(self) -> None:
        rw = _load_reply_watcher()
        import runtime.engine as engine

        with patch.object(engine, "send_telegram_message", return_value=None), patch.object(
            engine, "notify_operator_deduped", return_value="sent_first"
        ) as fallback:
            result = rw._fire_alert(None, self.CTX, self.ROW, "", dry_run=False, verbose=False)
        self.assertEqual(result, "fallback_agent:sent_first")
        fallback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
