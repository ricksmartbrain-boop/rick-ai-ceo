"""Hermetic session environment for the test suite.

Set at import time (pytest imports conftest before any test module) so that
module-level constants in runtime/ — DATA_ROOT, MEMORY_INDEX_FILE, DB paths —
bind to an isolated tmp tree instead of the real ~/rick-vault / runtime DB.

Three hard guarantees:
1. No test may spawn the real `openclaw` CLI. runtime/subagents.py defaults
   RICK_OPENCLAW_BIN to `openclaw` (`--timeout 900`): a test that forgets to
   neuter it makes real gateway calls, burns tokens, and hangs for minutes.
   /usr/bin/false exits 1 instantly and delegation fails loud.
2. No test may read or write the real vault, runtime DB, or config/rick.env
   (real keys + LIVE flags — scripts `source` it when RICK_ENV_FILE is unset).
3. No test may hold real credentials or open a network connection.
   runtime/winback_scheduler.py loads config/rick.env into os.environ with
   setdefault() AT IMPORT TIME — pytest collection imports it, which used to
   inject the production Telegram bot token (and every other key) into the
   test process, and notify_operator's direct Bot API path then sent REAL
   Telegram messages from the suite. Pre-setting dummy values here makes
   those setdefault() calls no-ops, and the socket guard below turns any
   remaining outbound connection attempt into a loud error.

Tests keep full control: these are plain os.environ defaults, not monkeypatch
locks. A test that sets its own RICK_* values in setUp (and reloads the module
under test) overrides everything here, exactly as before.
"""
from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

_HERMETIC_ROOT = Path(tempfile.mkdtemp(prefix="rick-hermetic-tests-"))
_VAULT = _HERMETIC_ROOT / "rick-vault"
for _sub in ("control", "memory", "operations", "revenue", "runtime", "scorecards", "dashboards", "logs"):
    (_VAULT / _sub).mkdir(parents=True, exist_ok=True)

# Empty env file: scripts must not source the real config/rick.env.
_ENV_FILE = _HERMETIC_ROOT / "rick.env"
_ENV_FILE.write_text("", encoding="utf-8")

os.environ.update(
    {
        # Guarantee 1: never the real gateway CLI.
        "RICK_OPENCLAW_BIN": "/usr/bin/false",
        "RICK_OPENCLAW_EVENT_BIN": "/usr/bin/false",
        "RICK_XPOST_BIN": "/usr/bin/false",
        # Guarantee 2: every data root / mutable file at the isolated tmp tree.
        "RICK_DATA_ROOT": str(_VAULT),
        "RICK_RUNTIME_DB_FILE": str(_VAULT / "runtime" / "rick-runtime.db"),
        "RICK_ENV_FILE": str(_ENV_FILE),
        "RICK_MEMORY_DIR": str(_VAULT / "memory"),
        "RICK_MEMORY_INDEX_FILE": str(_VAULT / "control" / "memory-index.json"),
        "RICK_MEMORY_ACCESS_LOG_FILE": str(_VAULT / "operations" / "memory-access.jsonl"),
        "RICK_MEMORY_OVERVIEW_FILE": str(_VAULT / "dashboards" / "memory-overview.md"),
        "RICK_EXECUTION_LEDGER_FILE": str(_VAULT / "operations" / "execution-ledger.jsonl"),
        "RICK_LLM_USAGE_LOG_FILE": str(_VAULT / "operations" / "llm-usage.jsonl"),
        "RICK_DREAMS_FILE": str(_HERMETIC_ROOT / "DREAMS.md"),
        "RICK_CAPS_CACHE": str(_HERMETIC_ROOT / "capabilities-last.json"),
        "RICK_OPENCLAW_WORKSPACE": str(_HERMETIC_ROOT / "openclaw-workspace"),
        # Guarantee 3: occupy every credential key config/rick.env carries so
        # winback_scheduler's import-time setdefault() cannot inject the real
        # values. Dummies, not deletions — child processes inherit os.environ
        # and must never see production secrets either.
        "RICK_TELEGRAM_BOT_TOKEN": "000000:hermetic-test-dummy",
        "RESEND_API_KEY": "hermetic-test-dummy",
        "STRIPE_SECRET_KEY": "hermetic-test-dummy",
        "OPENAI_API_KEY": "hermetic-test-dummy",
        "ANTHROPIC_API_KEY": "hermetic-test-dummy",
        "ELEVENLABS_API_KEY": "hermetic-test-dummy",
        "GMAIL_APP_PASSWORD": "hermetic-test-dummy",
        "MEMELORD_API_KEY": "hermetic-test-dummy",
        "MOLTBOOK_API_KEY": "hermetic-test-dummy",
        "SOCIAVAULT_API_KEY": "hermetic-test-dummy",
        "ROAST_INGEST_SECRET": "hermetic-test-dummy",
    }
)


# Guarantee 3, enforcement: the suite is hermetic, so no test has any business
# opening a TCP connection off-box. Fail loud (Rule 12) instead of silently
# reaching api.telegram.org / api.resend.com with whatever is in the env.
_real_connect = socket.socket.connect


def _hermetic_connect(self, address):  # noqa: ANN001 - stdlib signature
    if self.family == getattr(socket, "AF_UNIX", object()):
        return _real_connect(self, address)
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and host in ("127.0.0.1", "::1", "localhost"):
        return _real_connect(self, address)
    raise RuntimeError(
        f"hermetic test suite blocked outbound network connection to {host!r} "
        "(tests/conftest.py)"
    )


socket.socket.connect = _hermetic_connect
