#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path

env_file = Path.home() / 'clawd' / 'config' / 'rick.env'
if env_file.exists():
    with env_file.open() as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)

cmd = ['python3', str(Path.home() / 'rick-vault' / 'scripts' / 'nurture-dispatch.py')]
proc = subprocess.run(cmd, text=True, capture_output=True, env=os.environ.copy())
if proc.stdout:
    print(proc.stdout, end='')
if proc.stderr:
    print(proc.stderr, end='', file=os.sys.stderr)
raise SystemExit(proc.returncode)
