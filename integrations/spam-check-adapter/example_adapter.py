#!/usr/bin/env python3
"""
Reference adapter for Parcella's spam-check API contract -- see
README.md in this folder before using this. Standard library only, on
purpose: this is meant to be read and adapted, not deployed as-is.

Do NOT run this in production. score_message() below doesn't call a
real spam-check provider -- it exists only to prove the request/
response shape matches what Parcella expects. Replace it with a call
to Akismet, apilayer, a self-hosted filter like rspamd, or whatever
provider you actually want.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# Set to require a Bearer token matching Parcella's configured
# "Spam: external API key" -- leave unset to accept any request
# (fine for local testing, not for anything reachable from outside
# your own network).
EXPECTED_API_KEY = os.environ.get("ADAPTER_API_KEY")


def score_message(sender_email: str, subject: str, content: str) -> float:
    """Replace this with a real spam-check call. Must return a float
    between 0.0 (definitely not spam) and 1.0 (definitely spam)."""
    text = f"{subject} {content}".lower()
    return 1.0 if ("viagra" in text or "casino" in text) else 0.0


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/check":
            self._respond(404, {"error": "not found"})
            return

        if EXPECTED_API_KEY:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {EXPECTED_API_KEY}":
                self._respond(401, {"error": "unauthorized"})
                return

        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length))
            score = score_message(
                payload.get("sender_email", ""), payload.get("subject", ""), payload.get("content", ""),
            )
        except (ValueError, TypeError):
            self._respond(400, {"error": "invalid request body"})
            return

        self._respond(200, {"spam_score": score})

    def _respond(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass  # quiet by default -- replace with real logging for anything long-lived


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    print(f"Reference spam-check adapter listening on http://0.0.0.0:{port}/check")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
