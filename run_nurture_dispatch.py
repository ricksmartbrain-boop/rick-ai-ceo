import os
import subprocess
from pathlib import Path

# Load simple KEY=VALUE exports from the env file.
env_path = Path.home() / 'clawd' / 'config' / 'rick.env'
with env_path.open() as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):]
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        k = k.strip()
        v = v.strip().strip("'").strip('"')
        os.environ.setdefault(k, v)

proc = subprocess.run(
    ['python3', str(Path.home() / 'rick-vault' / 'scripts' / 'nurture-dispatch.py')],
    text=True,
    capture_output=True,
)
print(proc.stdout, end='')
print(proc.stderr, end='')
raise SystemExit(proc.returncode)
