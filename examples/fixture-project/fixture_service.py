"""Illustrative local HTTP fixture, not an authentic Decagon service."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/v1/hooks/inbound":
            self.send_error(404)
            return
        if self.headers.get("Authorization") not in {
            "Bearer fixture-test-key",
            "Bearer fixture-live-key",
        }:
            self.send_error(401)
            return
        try:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        except (KeyError, ValueError, json.JSONDecodeError):
            self.send_error(400)
            return

        if request.get("record_id") == "error-case":
            response = {
                "success": False,
                "error": {"message": "fixture requested a business-error response"},
            }
        else:
            response = {
                "success": True,
                "data": {
                    "event": request.get("event"),
                    "record_id": request.get("record_id"),
                    "source": "local-fixture",
                },
            }
        encoded = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Local py_capsule fixture listening on http://127.0.0.1:8765", flush=True)
    server.serve_forever()
