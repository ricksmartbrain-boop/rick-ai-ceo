"""CDPClient — small Python wrapper around the chrome-cdp ports.

Two responsibilities:
  1. Probe whether a given chrome-cdp port is alive (so callers can fail fast
     instead of timing out inside Playwright).
  2. Shell out to JS scrapers (linkedin-dm-cdp.js, google-maps-cdp-scraper.js,
     etc.) with consistent timeout + JSON parsing.

The Chrome-side conventions (port → workspace) live in
~/clawd/config/chrome-cdp-ports.json so all callers share one map.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PORTS = {
    "general": 9222,
    "reddit": 9223,
    "maps": 9224,
    "linkedin": 9225,
}


def is_port_alive(port: int, timeout: int = 4) -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def resolve_port(name_or_port: str | int, env_var: str | None = None) -> int:
    if env_var:
        env_val = os.getenv(env_var)
        if env_val and env_val.isdigit():
            return int(env_val)
    if isinstance(name_or_port, int):
        return name_or_port
    if isinstance(name_or_port, str) and name_or_port.isdigit():
        return int(name_or_port)
    return DEFAULT_PORTS.get(str(name_or_port).lower(), 9222)


@dataclass
class CDPResult:
    ok: bool
    items: list[dict]      # for scrapers that emit jsonlines
    summary: dict          # final summary line
    raw_stdout: str
    raw_stderr: str
    exit_code: int


def run_js_scraper(script_path: str | Path, args: list[str], *, timeout_s: int = 120) -> CDPResult:
    """Invoke a JS CDP scraper, parsing jsonlines stdout into items + summary.

    The convention for our scrapers (linkedin-dm-cdp.js, google-maps-cdp-scraper.js):
      - Each result row prints one JSON object on stdout.
      - The LAST JSON object's "kind" field is "summary" (or the only line if
        the scraper just returns a single status payload).
    """
    cmd = ["node", str(script_path), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return CDPResult(False, [], {"status": "error", "reason": "timeout"}, "", f"timeout after {timeout_s}s", 124)

    items: list[dict] = []
    summary: dict = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("kind") == "summary":
            summary = obj
        elif obj.get("kind") == "item":
            items.append(obj)
        else:
            # Single-payload script (e.g. linkedin-dm-cdp.js) — treat as summary
            summary = obj

    return CDPResult(
        ok=(proc.returncode == 0),
        items=items,
        summary=summary,
        raw_stdout=proc.stdout,
        raw_stderr=proc.stderr,
        exit_code=proc.returncode,
    )
