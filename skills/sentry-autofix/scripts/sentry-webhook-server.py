#!/usr/bin/env python3
"""Lightweight Sentry webhook receiver.

Listens for Sentry webhook POSTs, validates them, and pipes to the autofix pipeline.
Run behind ngrok or a reverse proxy for production use.

Usage:
    python3 sentry-webhook-server.py --port 9876
    # Then configure Sentry webhook to POST to http://yourhost:9876/sentry
"""

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

WEBHOOK_SECRET = os.getenv("SENTRY_WEBHOOK_SECRET", "")
AUTOFIX_SCRIPT = Path(__file__).parent / "sentry-autofix.py"

if not WEBHOOK_SECRET:
    print("FATAL: SENTRY_WEBHOOK_SECRET is not set. Refusing to start without webhook authentication.", file=sys.stderr)
    sys.exit(1)


class SentryWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/sentry":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Verify signature if secret is configured.
        # Sentry docs use "Sentry-Hook-Signature"; some versions send
        # "X-Sentry-Hook-Signature" — check both for compatibility.
        if WEBHOOK_SECRET:
            signature = (
                self.headers.get("Sentry-Hook-Signature")
                or self.headers.get("X-Sentry-Hook-Signature")
                or ""
            )
            expected = hmac.new(
                WEBHOOK_SECRET.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Invalid signature")
                return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid JSON")
            return

        # Pipe to autofix pipeline
        try:
            result = subprocess.run(
                [sys.executable, str(AUTOFIX_SCRIPT), "webhook"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            response = result.stdout or result.stderr or "processed"
        except subprocess.TimeoutExpired:
            response = '{"status": "timeout"}'

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response.encode())

    def log_message(self, format, *args):
        print(f"[sentry-webhook] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description="Sentry webhook receiver")
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), SentryWebhookHandler)
    print(f"Sentry webhook server listening on {args.host}:{args.port}/sentry")
    server.serve_forever()


if __name__ == "__main__":
    main()
