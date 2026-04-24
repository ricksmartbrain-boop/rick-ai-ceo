#!/usr/bin/env python3
"""VARA attestation minter — hourly cron.

Walks customer_events, sums revenue per customer, mints attestations
when thresholds crossed ($1K / $5K / $10K / $50K / $100K). Idempotent:
the table's UNIQUE(customer_id, tier_usd) constraint makes re-runs safe.

Default behavior: dry-run if RICK_VARA_LIVE != 1. Logs to
~/rick-vault/operations/vara.jsonl on every run.

Use --force-live to override env (useful for testing).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.db import connect
from runtime.vara import scan_and_mint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-live", action="store_true",
                        help="Override RICK_VARA_LIVE env (mint for real)")
    parser.add_argument("--summary-only", action="store_true",
                        help="Only print existing attestation summary")
    args = parser.parse_args()

    con = connect()
    try:
        if args.summary_only:
            from runtime.vara import attestation_summary
            print(json.dumps(attestation_summary(con), indent=2))
            return 0

        live_env = os.getenv("RICK_VARA_LIVE", "").strip().lower() in ("1", "true", "yes")
        live = args.force_live or live_env
        result = scan_and_mint(con, dry_run=not live)

        # Notify on first-ever mint of each tier
        if result.get("minted"):
            try:
                from runtime.engine import notify_operator_deduped
                for m in result["minted"]:
                    notify_operator_deduped(
                        con,
                        f"🏅 VARA attestation minted: customer "
                        f"{m['customer_id_short']}... crossed ${m['tier_usd']:,} "
                        f"(hmac={m['hmac_sha256']})",
                        kind=f"vara_mint_tier_{m['tier_usd']}",
                        dedup_window_hours=24,
                        purpose="customer_milestone",  # urgent — bypasses dedup
                    )
            except Exception:
                pass

        print(json.dumps(result, indent=2, default=str))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
