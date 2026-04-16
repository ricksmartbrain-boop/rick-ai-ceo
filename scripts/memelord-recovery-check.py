import os
import subprocess
import sys
from urllib import request, error

URL = "https://www.memelord.com/api/v1/ai-meme"
TOKEN = os.getenv("MEMELORD_API_KEY", "")
DATA = b'{"prompt":"test","count":1}'

req = request.Request(
    URL,
    data=DATA,
    method="POST",
    headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    },
)

status = None
try:
    with request.urlopen(req, timeout=10) as resp:
        status = resp.getcode()
except error.HTTPError as e:
    status = e.code
except Exception:
    sys.exit(0)

if status != 200:
    sys.exit(0)

telegram_cmd = [
    "python3",
    "/Users/rickthebot/.openclaw/workspace/runtime/runner.py",
    "telegram",
    "--text",
    "Memelord API is back! Running pipeline now.",
    "--chat-id",
    "203132131",
]

pipeline_cmd = [
    "python3",
    "/Users/rickthebot/.openclaw/workspace/scripts/memelord-pipeline.py",
]

res = subprocess.run(telegram_cmd)
if res.returncode != 0:
    sys.exit(res.returncode)

res = subprocess.run(pipeline_cmd)
sys.exit(res.returncode)
