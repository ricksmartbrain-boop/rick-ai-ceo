import os
import subprocess
import sys

CURL_CMD = [
    "curl",
    "-s",
    "--max-time",
    "10",
    "-o",
    "/dev/null",
    "-w",
    "%{http_code}",
    "-X",
    "POST",
    "https://www.memelord.com/api/v1/ai-meme",
    "-H",
    f"Authorization: Bearer {os.getenv('MEMELORD_API_KEY', '')}",
    "-H",
    "Content-Type: application/json",
    "-d",
    '{"prompt":"test","count":1}',
]

TELEGRAM_CMD = [
    sys.executable,
    "/Users/rickthebot/.openclaw/workspace/runtime/runner.py",
    "telegram",
    "--text",
    "Memelord API is back! Running pipeline now.",
    "--chat-id",
    "203132131",
]

PIPELINE_CMD = [
    sys.executable,
    "/Users/rickthebot/.openclaw/workspace/scripts/memelord-pipeline.py",
]


def main() -> int:
    result = subprocess.run(CURL_CMD, capture_output=True, text=True)
    status = (result.stdout or "").strip()
    if status != "200":
        return 0

    telegram = subprocess.run(TELEGRAM_CMD)
    if telegram.returncode != 0:
        return telegram.returncode

    pipeline = subprocess.run(PIPELINE_CMD)
    return pipeline.returncode


if __name__ == "__main__":
    raise SystemExit(main())
