import os
import subprocess
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

URL = 'https://www.memelord.com/api/v1/ai-meme'
TOKEN = f'Bearer {os.getenv("MEMELORD_API_KEY", "")}'
DATA = b'{"prompt":"test","count":1}'

req = Request(URL, data=DATA, method='POST')
req.add_header('Authorization', TOKEN)
req.add_header('Content-Type', 'application/json')

status = None
try:
    with urlopen(req, timeout=10) as resp:
        status = resp.getcode()
except HTTPError as e:
    status = e.code
except (URLError, TimeoutError, Exception):
    sys.exit(0)

if status == 200:
    telegram = subprocess.run([
        'python3',
        '/Users/rickthebot/.openclaw/workspace/runtime/runner.py',
        'telegram',
        '--text',
        'Memelord API is back! Running pipeline now.',
        '--chat-id',
        '203132131',
    ])
    if telegram.returncode == 0:
        subprocess.run([
            'python3',
            '/Users/rickthebot/.openclaw/workspace/scripts/memelord-pipeline.py',
        ])
