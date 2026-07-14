#!/usr/bin/env python3
"""Generate pipeline daily report from pipeline.jsonl"""
import json, os
from collections import Counter

PIPELINE = os.path.expanduser("~/rick-vault/logs/pipeline.jsonl")
entries = []
with open(PIPELINE) as f:
    for line in f:
        line = line.strip()
        if line:
            try: entries.append(json.loads(line))
            except: pass

stages = Counter(e.get("stage","?") for e in entries)
channels = Counter(e.get("channel","?") for e in entries)
cities = Counter(e.get("city","?") for e in entries if e.get("city"))
categories = Counter(e.get("category","?") for e in entries if e.get("category"))

print("=== PIPELINE REPORT ===")
print(f"Total entries: {len(entries)}")
print(f"\nBy Stage:")
for s, c in stages.most_common(): print(f"  {s}: {c}")
print(f"\nBy Channel:")
for s, c in channels.most_common(): print(f"  {s}: {c}")
print(f"\nBy City:")
for s, c in cities.most_common(): print(f"  {s}: {c}")
print(f"\nBy Category:")
for s, c in categories.most_common(): print(f"  {s}: {c}")
print(f"\nConversion: {stages.get('replied',0)}/{stages.get('contacted',0)} replies")
