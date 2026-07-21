from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DAEMON_SH = ROOT_DIR / "scripts" / "run-daemon.sh"

# WHY this test exists (Rule 9): the daemon's sibling-liveness watchdog is the
# ONLY thing that revives launchd StartInterval agents after they wedge on
# sleep/wake churn. Twice (2026-07-20, 2026-07-21 send-session day) the reply
# rail wedged for ~23h and prospect replies went unseen because these agents
# were NOT in the watchdog's coverage list. This test fails the moment any
# reply-rail agent is dropped from the LIVENESS heredoc — a coverage
# regression that is otherwise invisible until a real reply is lost.
REPLY_RAIL = ("ai.rick.reply-watcher", "ai.rick.reply-router", "ai.rick.outbound")


def _liveness_entries() -> dict[str, tuple[str, int]]:
    """Parse the `agent|logfile|max_age_min` heredoc from run-daemon.sh."""
    text = DAEMON_SH.read_text(encoding="utf-8")
    block = re.search(r"<<'LIVENESS'\n(.*?)\nLIVENESS", text, re.DOTALL)
    assert block, "LIVENESS heredoc not found in run-daemon.sh"
    entries: dict[str, tuple[str, int]] = {}
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line:
            continue
        agent, logfile, max_age = line.split("|")
        entries[agent] = (logfile, int(max_age))
    return entries


class DaemonLivenessCoverageTests(unittest.TestCase):
    def test_reply_rail_agents_are_watched(self) -> None:
        entries = _liveness_entries()
        for agent in REPLY_RAIL:
            self.assertIn(
                agent, entries,
                f"{agent} missing from run-daemon.sh sibling-liveness list — "
                "the reply rail can wedge unwatched (see 2026-07-21 incident).",
            )

    def test_thresholds_are_sane(self) -> None:
        # A threshold must be a positive number of minutes; a zero/negative
        # value would kick every cycle (or never), both of which defeat the
        # throttle the watchdog relies on.
        for agent, (logfile, max_age) in _liveness_entries().items():
            self.assertGreater(max_age, 0, f"{agent} has non-positive threshold")
            self.assertLess(max_age, 24 * 60, f"{agent} threshold >24h is not a watchdog")
            self.assertTrue(logfile.endswith(".log"), f"{agent} logfile looks wrong: {logfile}")


if __name__ == "__main__":
    unittest.main()
