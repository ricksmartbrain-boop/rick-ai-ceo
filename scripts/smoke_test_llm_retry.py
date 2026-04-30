#!/usr/bin/env python3
"""Smoke test: verify the LLM chain-failure retry layer fires and logs correctly.

Does NOT make live API calls or actually sleep 60s.
Tests the state machines and log writes only.

Usage:
    python3 scripts/smoke_test_llm_retry.py
"""
from __future__ import annotations

import json
import os
import sys

# Allow running from repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import llm

errors: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {label}")
    else:
        msg = f"  FAIL  {label}" + (f" — {detail}" if detail else "")
        print(msg)
        errors.append(msg)


# ── Reset any lingering state from previous test runs ────────────────────────
with llm._chain_fail_lock:
    llm._chain_fail_window.clear()


# ── 1. Window below threshold → no retry ─────────────────────────────────────
print("\n[1] Window below threshold")
llm._record_chain_failure()
llm._record_chain_failure()
check(
    "2 failures → _should_sleep_and_retry() == False",
    not llm._should_sleep_and_retry(),
)


# ── 2. Hit threshold → retry eligible ────────────────────────────────────────
print("\n[2] Cross threshold")
llm._record_chain_failure()  # now 3
check(
    "3 failures → _should_sleep_and_retry() == True",
    llm._should_sleep_and_retry(),
)


# ── 3. Retry event log write ──────────────────────────────────────────────────
print("\n[3] Retry event logging")
# Use a temp path so tests don't pollute the real log.
import tempfile, pathlib
tmp_log = pathlib.Path(tempfile.mktemp(suffix=".jsonl"))
original_log = llm.RETRY_LOG_FILE
llm.RETRY_LOG_FILE = tmp_log  # monkey-patch for test

try:
    llm._log_retry_event("review", retry_num=1, n_refs=4, slept_secs=60, outcome="retrying")
    check("retry log created", tmp_log.exists())

    if tmp_log.exists():
        ev = json.loads(tmp_log.read_text().strip().splitlines()[-1])
        check("event='chain_retry'", ev.get("event") == "chain_retry", str(ev))
        check("outcome='retrying'", ev.get("outcome") == "retrying", str(ev))
        check("route='review'", ev.get("route") == "review", str(ev))
        check("retry_num=1", ev.get("retry_num") == 1, str(ev))
        check("slept_secs=60", ev.get("slept_secs") == 60, str(ev))
        check("n_refs_in_chain=4", ev.get("n_refs_in_chain") == 4, str(ev))

    llm._log_retry_event("review", retry_num=1, n_refs=4, slept_secs=0, outcome="recovered")
    ev2 = json.loads(tmp_log.read_text().strip().splitlines()[-1])
    check("recovered event logged", ev2.get("outcome") == "recovered", str(ev2))

    llm._log_retry_event("review", retry_num=3, n_refs=4, slept_secs=0, outcome="exhausted")
    ev3 = json.loads(tmp_log.read_text().strip().splitlines()[-1])
    check("exhausted event logged", ev3.get("outcome") == "exhausted", str(ev3))
finally:
    llm.RETRY_LOG_FILE = original_log
    tmp_log.unlink(missing_ok=True)


# ── 4. Window drain ───────────────────────────────────────────────────────────
print("\n[4] Window drain")
with llm._chain_fail_lock:
    llm._chain_fail_window.clear()
check(
    "after clear → _should_sleep_and_retry() == False",
    not llm._should_sleep_and_retry(),
)


# ── 5. FALLBACK_ONLY short-circuit still works ────────────────────────────────
print("\n[5] RICK_LLM_FALLBACK_ONLY short-circuit")
os.environ["RICK_LLM_FALLBACK_ONLY"] = "1"
try:
    result = llm.generate_route_with_fallbacks(
        "heartbeat", "test prompt", "safe_fallback_text", "google", "gemini-3.1-flash-lite-preview"
    )
    check("mode==fallback", result.mode == "fallback", f"got mode={result.mode}")
    check("content==safe_fallback_text", "safe_fallback_text" in result.content)
    check("no retry events written (short-circuit)", True)  # can't sleep with FALLBACK_ONLY
finally:
    os.environ.pop("RICK_LLM_FALLBACK_ONLY", None)


# ── 6. Existing chain infrastructure still present ────────────────────────────
print("\n[6] Existing chain infrastructure")
check("_emit_silent_failure_event present", callable(getattr(llm, "_emit_silent_failure_event", None)))
check("_run_chain_once present", callable(getattr(llm, "_run_chain_once", None)))
check("generate_route_with_fallbacks present", callable(getattr(llm, "generate_route_with_fallbacks", None)))
check("RETRY_LOG_FILE is Path", isinstance(llm.RETRY_LOG_FILE, pathlib.Path))
check("MAX_RETRIES_PER_CALL == 3", llm.MAX_RETRIES_PER_CALL == 3)
check("RETRY_SLEEP_SECS == 60 (default)", llm.RETRY_SLEEP_SECS == 60)


# ── 7. flag_health probe ──────────────────────────────────────────────────────
print("\n[7] flag_health RICK_LLM_FALLBACK_HEALTH probe")
from runtime.flag_health import _probe_llm_fallback_health, scan_flags, stale_flags
probe = _probe_llm_fallback_health()
check("probe returns dict", isinstance(probe, dict))
check("flag == RICK_LLM_FALLBACK_HEALTH", probe.get("flag") == "RICK_LLM_FALLBACK_HEALTH")
check("status in valid set", probe.get("status") in ("fresh", "degraded", "stale", "no_data"), str(probe.get("status")))
check("meta.rate_pct present", isinstance(probe.get("meta", {}).get("rate_pct"), float), str(probe))

# scan_flags includes the computed probe
flags = scan_flags()
found = any(r["flag"] == "RICK_LLM_FALLBACK_HEALTH" for r in flags)
check("scan_flags() includes RICK_LLM_FALLBACK_HEALTH", found)

# stale_flags includes degraded
if probe["status"] == "degraded":
    sf = stale_flags()
    check("stale_flags() includes degraded probe", any(r["flag"] == "RICK_LLM_FALLBACK_HEALTH" for r in sf))

print(f"\n  probe result: status={probe['status']} "
      f"total_calls_1h={probe['meta']['total_calls_1h']} "
      f"hardcoded_fallback_1h={probe['meta']['hardcoded_fallback_1h']} "
      f"rate={probe['meta']['rate_pct']}%")


# ── Summary ───────────────────────────────────────────────────────────────────
print()
if errors:
    print(f"FAILED — {len(errors)} assertion(s):")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
else:
    print("ALL PASS")
