import os
import subprocess
import sys
from urllib import request, error

URL = 'https://www.memelord.com/api/v1/ai-meme'
DATA = b'{"prompt":"test","count":1}'
HEADERS = {
    'Authorization': f'Bearer {os.getenv("MEMELORD_API_KEY", "")}',
    'Content-Type': 'application/json',
}


def main() -> int:
    req = request.Request(URL, data=DATA, headers=HEADERS, method='POST')
    try:
        with request.urlopen(req, timeout=10) as resp:
            status = resp.getcode()
    except Exception:
        return 0

    if status != 200:
        return 0

    telegram = subprocess.run(
        [
            'python3',
            '/Users/rickthebot/.openclaw/workspace/runtime/runner.py',
            'telegram',
            '--text',
            'Memelord API is back! Running pipeline now.',
            '--chat-id',
            '203132131',
        ],
        check=False,
    )
    if telegram.returncode != 0:
        return telegram.returncode

    pipeline = subprocess.run(
        ['python3', '/Users/rickthebot/.openclaw/workspace/scripts/memelord-pipeline.py'],
        check=False,
    )
    return pipeline.returncode


if __name__ == '__main__':
    sys.exit(main())
