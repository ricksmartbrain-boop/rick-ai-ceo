#!/usr/bin/env python3
"""
Run new-leads-pipeline across a state seed list in batches.

Usage:
  python3 scripts/state-places-sweep.py \
    --seed ~/rick-vault/projects/outreach/state-city-seed-all.json \
    --categories "dentist,chiropractor,med spa" \
    --batch-size 10 \
    --offset 0 \
    --per-combo 4 \
    --limit 20 \
    --send
"""

import argparse
import json
import subprocess
from pathlib import Path

PIPELINE = Path('/Users/rickthebot/.openclaw/workspace/scripts/new-leads-pipeline.py')
DEFAULT_SEED = Path.home() / 'rick-vault/projects/outreach/state-city-seed-all.json'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', default=str(DEFAULT_SEED))
    parser.add_argument('--categories', required=True)
    parser.add_argument('--batch-size', type=int, default=10)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--per-combo', type=int, default=4)
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--send', action='store_true')
    args = parser.parse_args()

    seed_path = Path(args.seed).expanduser()
    entries = json.loads(seed_path.read_text())
    batch = entries[args.offset:args.offset + args.batch_size]
    if not batch:
        print('No entries in selected batch')
        return

    cities = ','.join(item['city'] for item in batch)
    cmd = [
        'python3', str(PIPELINE),
        '--cities', cities,
        '--categories', args.categories,
        '--per-combo', str(args.per_combo),
        '--limit', str(args.limit),
    ]
    if args.send:
        cmd.append('--send')

    print('Running batch:')
    print(f'  offset={args.offset}')
    print(f'  batch_size={len(batch)}')
    print(f'  cities={cities}')
    print(f'  categories={args.categories}')
    subprocess.run(cmd, check=False)


if __name__ == '__main__':
    main()
