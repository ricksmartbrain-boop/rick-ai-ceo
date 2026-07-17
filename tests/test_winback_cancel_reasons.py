"""Producer-consumer key agreement for churn/cancel-reasons.jsonl.

The winback day-30 scheduler once read rows with the wrong keys ("email" /
"cancel_reason") while BOTH producers key the address as "customer" and carry
the actual feedback in verbatim_text (reply-triage) or comment/feedback
(stripe-poll survey harvest). Captured churn feedback never surfaced, and
drafts falsely told customers "you never told us why you left". These tests
pin the contract: winback reads exactly what the producers write, quotes only
human material, and never quotes machine enums back at a customer.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from runtime import winback_scheduler as wb


# Fixtures shaped EXACTLY like the two producers. If a producer renames a key,
# update these in the same commit — that is the point of this file.
def stripe_poll_row(email: str, *, feedback: str = "none", comment: str = "none",
                    reason: str = "cancellation_requested") -> dict:
    # scripts/stripe-poll.py _record_cancel_reason rec
    return {
        "ts": "2026-07-16T19:17:21",
        "customer": email,
        "customer_id": "cus_test",
        "source": "stripe-poll",
        "subscription_id": "sub_test",
        "subscription_status": "canceling",
        "end_date": "2026-08-13",
        "feedback": feedback,
        "comment": comment,
        "reason": reason,
        "details_sig": f"feedback={feedback}|comment={comment}|reason={reason}",
        "tag": "churn_feedback",
    }


def reply_triage_row(email: str, verbatim: str) -> dict:
    # scripts/reply-triage.py record_cancel_reason rec
    return {
        "ts": "2026-07-16T20:00:00",
        "customer": email,
        "customer_id": "cust_test",
        "customer_status": "canceling",
        "source": "reply-triage",
        "email_id": "msg_test",
        "subject": "Re: quick question before you go",
        "verbatim_text": verbatim,
        "tag": "churn_feedback",
    }


class CancelReasonKeyAgreementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.reasons_file = Path(self._tmp.name) / "cancel-reasons.jsonl"
        self._orig = wb.CANCEL_REASONS
        wb.CANCEL_REASONS = self.reasons_file

    def tearDown(self) -> None:
        wb.CANCEL_REASONS = self._orig
        self._tmp.cleanup()

    def _write(self, *rows: dict) -> None:
        self.reasons_file.write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

    def test_stripe_survey_answer_surfaces(self) -> None:
        self._write(stripe_poll_row("diane@example.com", feedback="too_expensive"))
        self.assertEqual(wb.cancel_reason_for("diane@example.com", {}), "too_expensive")

    def test_reply_verbatim_beats_survey_and_machine_enums_never_quoted(self) -> None:
        # A machine-only stripe row must yield "" — quoting
        # "cancellation_requested" at a customer is worse than silence...
        self._write(stripe_poll_row("russian@crushermail.com"))
        self.assertEqual(wb.cancel_reason_for("russian@crushermail.com", {}), "")
        # ...and a later reply-triage verbatim is the datum cd53425 exists to
        # collect: it must win.
        self._write(
            stripe_poll_row("russian@crushermail.com"),
            reply_triage_row("russian@crushermail.com", "Price doubled and I only use it in summer."),
        )
        self.assertEqual(
            wb.cancel_reason_for("russian@crushermail.com", {}),
            "Price doubled and I only use it in summer.",
        )

    def test_build_item_quotes_reason_and_reads_metadata_canceled_at(self) -> None:
        self._write(reply_triage_row("russian@crushermail.com", "Too pricey for me."))
        cand = {
            "customer_id": "cust_test",
            "email": "russian@crushermail.com",
            "name": "",
            "status": "canceled",
            # subscription_status_changed payloads carry no canceled_at —
            # customers.metadata does.
            "metadata": {"product_name": "LinguaLive Subscription",
                         "canceled_at": "2026-07-17"},
            "payload": {"end_date": "2026-08-13", "cancel_at_period_end": True},
            "signup_date": "2026-06-13",
            "end_date": "2026-08-13",
            "days_since_lapse": 30,
        }
        item = wb.build_item(cand)
        self.assertIn('you told us: "Too pricey for me."', item["body_markdown"])
        self.assertNotIn("never told us why", item["body_markdown"])
        self.assertEqual(item["cancel_reason"], "Too pricey for me.")
        self.assertIn("canceled 2026-07-17", item["note"])


if __name__ == "__main__":
    unittest.main()
