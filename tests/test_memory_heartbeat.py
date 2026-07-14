from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class MemoryIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tempdir.name)
        self.env_backup = os.environ.copy()
        os.environ.update(
            {
                "RICK_DATA_ROOT": str(self.data_root),
                "RICK_MEMORY_INDEX_FILE": str(self.data_root / "control" / "memory-index.json"),
                "RICK_MEMORY_ACCESS_LOG_FILE": str(self.data_root / "operations" / "memory-access.jsonl"),
                "RICK_MEMORY_OVERVIEW_FILE": str(self.data_root / "dashboards" / "memory-overview.md"),
            }
        )
        (self.data_root / "projects" / "partner-connector").mkdir(parents=True, exist_ok=True)
        (self.data_root / "memory").mkdir(parents=True, exist_ok=True)
        (self.data_root / "operations").mkdir(parents=True, exist_ok=True)
        (self.data_root / "control").mkdir(parents=True, exist_ok=True)
        (self.data_root / "dashboards").mkdir(parents=True, exist_ok=True)
        self.memory_module = load_module(
            "rick_memory_index_test",
            ROOT_DIR / "skills" / "obsidian-memory" / "scripts" / "rebuild-memory-index.py",
        )

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env_backup)
        self.tempdir.cleanup()

    def write_file(self, relative_path: str, content: str, days_ago: int) -> Path:
        path = self.data_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        ts = (self.memory_module.now().timestamp() - days_ago * 86400)
        os.utime(path, (ts, ts))
        return path

    def test_memory_index_tracks_hot_warm_cold_entries(self) -> None:
        self.write_file(
            "projects/partner-connector/summary.md",
            "---\ntype: project\n---\n# Partner Connector\nRecent launch notes\n",
            1,
        )
        self.write_file("memory/2026-02-20.md", "# Daily\nWarm note\n", 12)
        self.write_file("decisions/2025-12-01.md", "# Old Decision\nCold note\n", 65)

        index = self.memory_module.build_index()
        self.assertEqual(index["counts"]["entries"], 3)
        self.assertEqual(index["counts"]["tiers"].get("hot"), 1)
        self.assertEqual(index["counts"]["tiers"].get("warm"), 1)
        self.assertEqual(index["counts"]["tiers"].get("cold"), 1)

        self.memory_module.write_index(index)
        self.memory_module.write_dashboard(index)
        self.assertTrue((self.data_root / "control" / "memory-index.json").exists())
        self.assertTrue((self.data_root / "dashboards" / "memory-overview.md").exists())

    def test_query_records_access_and_filters_by_project(self) -> None:
        self.write_file(
            "projects/partner-connector/summary.md",
            "---\ntype: project\n---\n# Partner Connector\nLaunch system and revenue notes\n",
            2,
        )
        self.write_file("projects/info-products/summary.md", "# Info Products\nSecondary notes\n", 2)

        index = self.memory_module.build_index()
        results = self.memory_module.query_entries(index["entries"], search="launch", project="partner-connector")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["project"], "partner-connector")

        self.memory_module.append_access_entries(results)
        log_text = (self.data_root / "operations" / "memory-access.jsonl").read_text(encoding="utf-8")
        self.assertIn("partner-connector/summary.md", log_text)


class WatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tempdir.name)
        self.env_backup = os.environ.copy()
        os.environ.update(
            {
                "RICK_DATA_ROOT": str(self.data_root),
                "RICK_WATCHDOG_PROCESSES_FILE": str(self.data_root / "config" / "watchdog-processes.json"),
                "RICK_WATCHDOG_STATE_FILE": str(self.data_root / "operations" / "watchdog-state.json"),
                "RICK_TMUX_SOCKET_PATH": str(self.data_root / "tmux.sock"),
            }
        )
        (self.data_root / "config").mkdir(parents=True, exist_ok=True)
        (self.data_root / "control").mkdir(parents=True, exist_ok=True)
        (self.data_root / "operations").mkdir(parents=True, exist_ok=True)
        (self.data_root / "config" / "watchdog-processes.json").write_text(
            json.dumps(
                {
                    "processes": [
                        {
                            "name": "Rick Daemon",
                            "enabled": True,
                            "kind": "process",
                            "match": "runtime/runner.py heartbeat",
                            "restart_cmd": "bash ~/clawd/scripts/run-daemon.sh",
                            "cooldown_seconds": 60,
                            "max_restarts_per_day": 3,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.watchdog_module = load_module(
            "rick_watchdog_test",
            ROOT_DIR / "skills" / "self-healing-ops" / "scripts" / "watchdog.py",
        )

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env_backup)
        self.tempdir.cleanup()

    def test_watchdog_restarts_missing_process_and_records_state(self) -> None:
        with patch.object(
            self.watchdog_module,
            "process_healthy",
            side_effect=[(False, "missing"), (True, "1234")],
        ), patch.object(
            self.watchdog_module,
            "execute_restart",
            return_value=subprocess.CompletedProcess(["bash"], 0, stdout="ok", stderr=""),
        ):
            results = self.watchdog_module.run_watchdog()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "restarted")
        self.assertEqual(results[0].status, "pass")

        state = json.loads((self.data_root / "operations" / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertIn("Rick Daemon", state)
        recovery_log = (self.data_root / "control" / "recovery-log.md").read_text(encoding="utf-8")
        self.assertIn("Rick Daemon", recovery_log)


if __name__ == "__main__":
    unittest.main()
