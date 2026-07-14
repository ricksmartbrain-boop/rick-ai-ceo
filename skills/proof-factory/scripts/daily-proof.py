#!/usr/bin/env python3
"""Generate Rick's daily proof-of-work report card.

Standalone script that can be run via cron at 9pm daily.
Usage: python3 daily-proof.py [--type daily|case_study|weekly_bip]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from runtime.db import connect, init_db
from runtime.engine import queue_proof_workflow, work


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate proof-of-work content")
    parser.add_argument("--type", default="daily", choices=["daily", "case_study", "weekly_bip"])
    parser.add_argument("--execute", action="store_true", help="Also execute the workflow immediately")
    args = parser.parse_args()

    connection = connect()
    init_db(connection)

    workflow_id = queue_proof_workflow(connection, proof_type=args.type)
    print(f"Queued proof workflow: {workflow_id} (type: {args.type})")

    if args.execute:
        results = work(connection, limit=5)
        for r in results:
            print(f"  Step: {r.get('status', 'unknown')} — {r.get('summary', '')[:80]}")

    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
