import os
import subprocess

check_cmd = [
    "curl", "-s", "--max-time", "10", "-o", "/dev/null", "-w", "%{http_code}",
    "-X", "POST", "https://www.memelord.com/api/v1/ai-meme",
    "-H", f"Authorization: Bearer {os.getenv('MEMELORD_API_KEY', '')}",
    "-H", "Content-Type: application/json",
    "-d", '{"prompt":"test","count":1}'
]

result = subprocess.run(check_cmd, capture_output=True, text=True)
status = result.stdout.strip()

if status == "200":
    subprocess.run([
        "python3", "/Users/rickthebot/.openclaw/workspace/runtime/runner.py",
        "telegram", "--text", "Memelord API is back! Running pipeline now.", "--chat-id", "203132131"
    ], check=False)
    subprocess.run([
        "python3", "/Users/rickthebot/.openclaw/workspace/scripts/memelord-pipeline.py"
    ], check=False)
