"""Reply-rail tests — 2026-07-19 noise drop-list, save-offer fast-path, and
the reply→deal_close handoff fix.

WHY these tests exist (Rule 9 — intent, not just behavior):
  1. Platform noise (Product Hunt / LinkedIn / GitHub / bounces) must die AT
     THE INGESTION CHOKEPOINT, deterministically and LOUDLY (counted log
     lines) — but a save-offer customer or existing customer must NEVER be
     droppable, no matter what future rule is added.
  2. A reply-activated save-offer customer writing in must page Vlad without
     waiting for any classifier loop — Simone lapsing today means a 10-min
     classifier delay is a lost save.
  3. A sales-intent reply from a real human must produce a deal_close
     workflow that the owner's approval actually RUNS — not the pre-2026-07-19
     Potemkin shape where approval closed a final-step no-op workflow
     (Nikola/Zeno class of loss).

Runs against an isolated RICK_DATA_ROOT + temp DB. Never touches the
production vault, never sends anything (telegram/LLM/subprocess stubbed).

Run standalone (env must be set before runtime.* import):
    python3 -m pytest tests/test_reply_rail_2026_07_19.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# --- Isolated data root: MUST be in env before any runtime.* import ---------
_TMP = tempfile.mkdtemp(prefix="rick-reply-rail-test-")
os.environ.update({
    "RICK_DATA_ROOT": _TMP,
    "RICK_RUNTIME_DB_FILE": str(Path(_TMP) / "runtime" / "rick-runtime.db"),
    "RICK_EXECUTION_LEDGER_FILE": str(Path(_TMP) / "operations" / "execution-ledger.jsonl"),
    "RICK_LLM_USAGE_LOG_FILE": str(Path(_TMP) / "operations" / "llm-usage.jsonl"),
    "RICK_PORTFOLIO_SCORECARDS_FILE": str(ROOT_DIR / "config" / "portfolio-scorecards.example.json"),
    "RICK_LANE_POLICY_FILE": str(ROOT_DIR / "config" / "lane-policy.example.json"),
    "RICK_ENV_FILE": str(Path(_TMP) / "no-such-rick.env"),  # never load prod creds
})

from runtime import db as rick_db  # noqa: E402
from runtime import engine as rick_engine  # noqa: E402
from runtime import reply_router as rick_router  # noqa: E402
from runtime.inbound import imap_watcher as imapw  # noqa: E402


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


reply_watcher = _load_script("reply_watcher_script", ROOT_DIR / "scripts" / "reply-watcher.py")
reply_classifier = _load_script(
    "reply_classifier_script",
    ROOT_DIR / "skills" / "email-automation" / "scripts" / "reply-classifier.py",
)

TRIAGE_DIR = Path(_TMP) / "mailbox" / "triage"
UTC_TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fresh_conn():
    conn = rick_db.connect()
    return conn


def _seed_customers(conn):
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        ("cust_simone", "haeuslsi@gmail.com", "Simone Haeusler"),
        ("cust_nat", "nat.waterworth@gmail.com", "Nat Waterworth"),
        ("cust_diane", "diane@factotem.com", "Diane Bloom"),
        ("cust_russian", "russian@crushermail.com", ""),
        ("cust_west", "westcoscia@duck.com", ""),
    ]
    for cid, email, name in rows:
        conn.execute(
            "INSERT OR IGNORE INTO customers (id, email, name, created_at, updated_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, email, name, now, now, now),
        )
    conn.commit()


class FakeIMAP:
    """Just enough imaplib surface for process_messages: search/fetch/store."""

    def __init__(self, raw_messages: list[bytes]):
        self._msgs = raw_messages
        self.stored: list[bytes] = []

    def search(self, charset, criteria):
        ids = b" ".join(str(i + 1).encode() for i in range(len(self._msgs)))
        return "OK", [ids]

    def fetch(self, uid, spec):
        idx = int(uid.decode()) - 1
        return "OK", [(uid + b" (BODY.PEEK[])", self._msgs[idx])]

    def store(self, uid, flags, value):
        self.stored.append(uid)
        return "OK", [b""]


def _raw_email(from_hdr: str, subject: str, body: str, msgid: str) -> bytes:
    return (
        f"From: {from_hdr}\r\n"
        f"To: rick@meetrick.ai\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{msgid}>\r\n"
        f"Date: Sat, 19 Jul 2026 09:00:00 +0000\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n{body}\r\n"
    ).encode("utf-8")


# Modeled on Nikola Djordjevic's real 2026-04 reply shape (warm human,
# answers questions, asks about pricing + next step). NOT the arjun persona.
MILAN_EMAIL = "milan.petrovic@heyvoxlane.com"
MILAN_BODY = (
    "Hi Rick,\r\n\r\nThanks for reaching out, these are great questions.\r\n\r\n"
    "1. We run a two-person team and the calendar chaos is real.\r\n"
    "2. I looked at your site — what does the Managed plan actually include "
    "day to day?\r\n"
    "3. How do your pricing tiers break down for a small team like ours?\r\n\r\n"
    "Could we do a 15-minute call this week? Wednesday afternoon works.\r\n\r\n"
    "Best,\r\nMilan"
)


class NoiseDropListTests(unittest.TestCase):
    """Task 1 — deterministic drop-list at the ingestion chokepoint."""

    @classmethod
    def setUpClass(cls):
        cls.conn = _fresh_conn()
        rick_db.init_db(cls.conn)
        _seed_customers(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_drop_rules_match_platform_noise(self):
        cases = {
            "notifications@producthunt.com": "producthunt",
            "hello@producthunt.com": "producthunt",
            "messages-noreply@linkedin.com": "linkedin",
            "notifications-noreply@linkedin.com": "linkedin",
            "notifications@github.com": "github",
            "noreply@github.com": "github",
            "postmaster@somecorp.com": "bounce",
            "mailer-daemon@googlemail.com": "bounce",
        }
        for addr, expected in cases.items():
            self.assertEqual(imapw.noise_drop_rule(addr), expected, addr)

    def test_drop_rules_never_match_humans(self):
        # A LinkedIn EMPLOYEE or a real prospect must never match — the rules
        # target noreply/notification localparts, not whole human domains.
        for addr in (MILAN_EMAIL, "jane.doe@linkedin.com", "sam@github.com",
                     "haeuslsi@gmail.com", "nikoladjordjevic@heyrumora.com"):
            self.assertIsNone(imapw.noise_drop_rule(addr), addr)

    def test_protected_senders_are_never_droppable(self):
        # Save-offer addresses + existing customers (email or domain) are
        # exempt even if a future rule would match them.
        for addr in ("haeuslsi@gmail.com", "nat.waterworth@gmail.com",
                     "diane@factotem.com", "russian@crushermail.com",
                     "westcoscia@duck.com", "billing@factotem.com"):
            self.assertTrue(imapw._protected_sender(self.conn, addr), addr)
        self.assertFalse(imapw._protected_sender(self.conn, "notifications@producthunt.com"))

    def test_chokepoint_drops_noise_keeps_humans_and_saves(self):
        """E2E at the chokepoint: noise never reaches triage, humans and
        save-offer customers always do, and every drop is counted + logged."""
        msgs = [
            _raw_email("Product Hunt <notifications@producthunt.com>",
                       "Your daily digest", "Today on Product Hunt...", "ph-1@producthunt.com"),
            _raw_email("LinkedIn <messages-noreply@linkedin.com>",
                       "You have a new message", "See your message...", "li-1@linkedin.com"),
            _raw_email("GitHub <notifications@github.com>",
                       "[repo] Issue #12", "New issue opened", "gh-1@github.com"),
            _raw_email("Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                       "Delivery Status Notification", "bounce", "bounce-1@googlemail.com"),
            _raw_email("Simone Haeusler <haeuslsi@gmail.com>",
                       "Re: your offer", "I saw your email — can we talk about keeping my plan?",
                       "simone-1@gmail.com"),
            _raw_email("Milan Petrovic <milan.petrovic@heyvoxlane.com>",
                       "Re: quick idea for Meetrick", "Interested — what are the pricing tiers?",
                       "milan-0@heyvoxlane.com"),
        ]
        mail = FakeIMAP(msgs)
        summary = imapw.process_messages(self.conn, "INBOX", mail, "UNSEEN", 50, dry_run=False)

        self.assertEqual(summary["noise_dropped"], 4)
        self.assertEqual(summary["noise_dropped_by_rule"],
                         {"producthunt": 1, "linkedin": 1, "github": 1, "bounce": 1})
        # Two survivors reach triage
        self.assertEqual(summary["triage_rows"], 2)
        triage_files = sorted(TRIAGE_DIR.glob("inbound-*.jsonl"))
        self.assertTrue(triage_files)
        rows = [json.loads(l) for f in triage_files
                for l in f.read_text().splitlines() if l.strip()]
        senders = {r["from"] for r in rows}
        self.assertIn("haeuslsi@gmail.com", senders)
        self.assertIn(MILAN_EMAIL, senders)
        for noisy in ("notifications@producthunt.com", "messages-noreply@linkedin.com",
                      "notifications@github.com", "mailer-daemon@googlemail.com"):
            self.assertNotIn(noisy, senders)
        # Save-offer row stamped at ingestion
        simone_row = next(r for r in rows if r["from"] == "haeuslsi@gmail.com")
        self.assertTrue(simone_row.get("save_offer"))
        # Silent-drop is forbidden: each drop logged with a running count
        log_path = Path(_TMP) / "operations" / "imap-watcher.jsonl"
        drop_events = [json.loads(l) for l in log_path.read_text().splitlines()
                       if '"noise-drop"' in l]
        self.assertEqual(len(drop_events), 4)
        self.assertEqual({e["dropped_this_run"] for e in drop_events}, {1, 2, 3, 4})
        # Clean up triage rows so later tests control their own fixture files
        for f in triage_files:
            f.unlink()


class SaveOfferFastPathTests(unittest.TestCase):
    """Task 2 — save-address inbound bypasses classification, pages Vlad P0."""

    @classmethod
    def setUpClass(cls):
        cls.conn = _fresh_conn()
        rick_db.init_db(cls.conn)
        _seed_customers(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        TRIAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.triage_path = TRIAGE_DIR / f"inbound-{UTC_TODAY}.jsonl"

    def tearDown(self):
        if self.triage_path.exists():
            self.triage_path.unlink()
        state = Path(_TMP) / "control" / "reply-watcher-state.json"
        if state.exists():
            state.unlink()

    def test_unclassified_save_row_is_picked_up(self):
        """The fast-path must NOT wait for the classifier: an unclassified row
        from a save address is watch-listed immediately (late replies too —
        there is no date gate to fail)."""
        row = {"id": "saverow0001nat00", "from": "nat.waterworth@gmail.com",
               "from_name": "Nat Waterworth", "subject": "Re: your note",
               "body": "Hey — just saw this. Is the offer still on?",
               "received_at": "Sat, 19 Jul 2026 08:00:00 +0000",
               "ingested_at": datetime.now().isoformat(timespec="seconds")}
        self.triage_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        rows = reply_watcher._load_watch_rows(handled={})
        self.assertEqual(len(rows), 1)
        ctx = reply_watcher._row_ctx(rows[0])
        self.assertEqual(ctx["label"], "save_offer")

    def test_save_reply_fires_p0_alert_and_stages_draft(self):
        row = {"id": "saverow0002sim00", "from": "haeuslsi@gmail.com",
               "from_name": "Simone Haeusler", "subject": "Re: keeping LinguaLive",
               "body": "I got your message — yes, I would like to keep my subscription if the offer stands.",
               "received_at": "Sat, 19 Jul 2026 09:30:00 +0000",
               "ingested_at": datetime.now().isoformat(timespec="seconds")}
        self.triage_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

        sent: list[str] = []

        def fake_tg(conn, text, **kwargs):
            sent.append(text)
            return 424242

        fake_llm = lambda route, prompt, fallback="": SimpleNamespace(  # noqa: E731
            mode="live", content="Hi Simone — offer stands. — Rick", model="stub", usage=None,
            notes="",
        )
        with patch.object(rick_engine, "send_telegram_message", fake_tg), \
             patch("runtime.llm.generate_text", fake_llm):
            result = reply_watcher.run(dry_run=False, verbose=False)

        self.assertEqual(result["new_replies"], 1)
        self.assertEqual(len(sent), 1)
        self.assertIn("SAVE-OFFER REPLY — Simone Haeusler — offer is reply-activated", sent[0])
        self.assertIn("🚨 P0", sent[0])
        draft_path = Path(_TMP) / "mailbox" / "drafts" / "auto" / "saverow0002sim00.json"
        self.assertTrue(draft_path.exists(), "draft must be staged for review")
        draft = json.loads(draft_path.read_text())
        self.assertFalse(draft["auto_send"])
        self.assertTrue(draft["review_required"])


class DealCloseHandoffTests(unittest.TestCase):
    """Task 3/4 — triage → classify → deal_close created → owner approval
    actually RUNS the pipeline (regression: pre-fix, approval closed a
    final-step no-op and the deal never advanced)."""

    @classmethod
    def setUpClass(cls):
        cls.conn = _fresh_conn()
        rick_db.init_db(cls.conn)
        _seed_customers(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        TRIAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.triage_path = TRIAGE_DIR / f"inbound-{UTC_TODAY}.jsonl"

    def tearDown(self):
        if self.triage_path.exists():
            self.triage_path.unlink()
        state = Path(_TMP) / "control" / "reply-watcher-state.json"
        if state.exists():
            state.unlink()

    def test_e2e_real_human_sales_reply_reaches_runnable_deal_close(self):
        # --- 1. triage (as ingested by imap-watcher) ---
        row = {"id": "milanrow0001e2e0", "message_id": "milan-1@heyvoxlane.com",
               "in_reply_to": "", "references": [], "thread_id": None,
               "from": MILAN_EMAIL, "from_name": "Milan Petrovic",
               "subject": "Re: quick idea for Meetrick", "body": MILAN_BODY,
               "has_html": False,
               "received_at": "Sat, 19 Jul 2026 10:15:00 +0000",
               "ingested_at": datetime.now().isoformat(timespec="seconds")}
        self.triage_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

        # --- 2. classify (LLM stubbed — routing must not depend on the model) ---
        reply_classifier.generate_text = lambda route, prompt, fallback="": SimpleNamespace(
            content="sales_inquiry")
        summary = reply_classifier.process_file(self.triage_path, dry_run=False, batch_cap=10)
        self.assertEqual(summary["classified"], 1)
        classified = json.loads(self.triage_path.read_text().splitlines()[0])
        self.assertEqual(classified["classification"], "sales_inquiry")

        # --- 3. route (subprocess stubbed: no telegram, no auto-draft LLM) ---
        with patch("subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, stdout="{}", stderr="")):
            drained = rick_router.drain(dry_run=False, batch_cap=10)
        self.assertEqual(drained["by_action"].get("approval-created"), 1)

        wf = self.conn.execute(
            "SELECT * FROM workflows WHERE kind='deal_close' AND context_json LIKE ? "
            "ORDER BY created_at DESC LIMIT 1", (f"%{MILAN_EMAIL}%",)).fetchone()
        self.assertIsNotNone(wf, "sales-intent reply must create a deal_close workflow row")
        self.assertEqual(wf["status"], "blocked")
        self.assertEqual(wf["stage"], "awaiting-approval")

        job = self.conn.execute(
            "SELECT * FROM jobs WHERE workflow_id=?", (wf["id"],)).fetchone()
        # THE FIX: parked at the pipeline START, not the final step. At the
        # final step, approval used to close the workflow as a completed no-op.
        self.assertEqual(job["step_name"], "lead_intake")
        self.assertEqual(job["step_index"], 0)
        self.assertEqual(job["status"], "blocked")

        apr = self.conn.execute(
            "SELECT * FROM approvals WHERE workflow_id=? AND status='open'", (wf["id"],)).fetchone()
        self.assertIsNotNone(apr)

        # --- 4. owner approves → pipeline must actually RUN ---
        with patch.object(rick_engine, "notify_operator", lambda *a, **k: None):
            res = rick_engine.resolve_approval(self.conn, apr["id"], "approved", "go", "telegram")
        self.assertEqual(res.get("requeued_job"), job["id"],
                         "approval must re-queue lead_intake, not skip it")
        job2 = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        wf2 = self.conn.execute("SELECT * FROM workflows WHERE id=?", (wf["id"],)).fetchone()
        self.assertEqual(job2["status"], "queued")
        self.assertEqual(wf2["status"], "active")
        self.assertNotEqual(wf2["status"], "done",
                            "regression guard: approval must not close the deal as a no-op")
        # The engine's own scheduler sees the job — the deal advances through
        # the EXISTING path (lead_intake → ... → pitch_send, itself owner-gated).
        runnable = rick_engine.next_runnable_job(self.conn)
        self.assertIsNotNone(runnable)
        self.assertEqual(runnable["id"], job["id"])

        # --- 5. draft staged + alert fired for the same reply (stubbed sender) ---
        sent: list[str] = []

        def fake_tg(conn, text, **kwargs):
            sent.append(text)
            return 555

        fake_llm = lambda route, prompt, fallback="": SimpleNamespace(  # noqa: E731
            mode="live", content="Hey Milan — happy to walk you through it. — Rick",
            model="stub", usage=None, notes="")
        with patch.object(rick_engine, "send_telegram_message", fake_tg), \
             patch("runtime.llm.generate_text", fake_llm):
            watch = reply_watcher.run(dry_run=False, verbose=False)
        self.assertEqual(watch["new_replies"], 1)
        self.assertEqual(len(sent), 1)
        self.assertIn(MILAN_EMAIL, sent[0])
        draft_path = Path(_TMP) / "mailbox" / "drafts" / "auto" / "milanrow0001e2e0.json"
        self.assertTrue(draft_path.exists(), "reply draft must be staged for Vlad")
        self.assertFalse(json.loads(draft_path.read_text())["auto_send"])

    def test_commitment_gate_stays_record_only(self):
        """The 2026-07-13 owner-only-commitments rule is untouched: payment /
        contract language parks at the FINAL step and approving it records the
        YES without launching any automated pipeline step."""
        row = {"id": "commitrow0001aaa", "from": "vendor@consultco.example",
               "from_name": "Vendor", "subject": "invoice attached",
               "body": "Please resend the payment and sign the contract.",
               "classification": "question",
               "received_at": "Sat, 19 Jul 2026 11:00:00 +0000",
               "ingested_at": datetime.now().isoformat(timespec="seconds")}
        self.triage_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        with patch("subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, stdout="{}", stderr="")):
            rick_router.drain(dry_run=False, batch_cap=10)
        wf = self.conn.execute(
            "SELECT * FROM workflows WHERE context_json LIKE '%vendor@consultco.example%' "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
        self.assertIsNotNone(wf)
        job = self.conn.execute(
            "SELECT * FROM jobs WHERE workflow_id=?", (wf["id"],)).fetchone()
        self.assertEqual(job["step_name"], "close_or_escalate")


if __name__ == "__main__":
    unittest.main()
