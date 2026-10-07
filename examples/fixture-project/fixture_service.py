"""Illustrative local HTTP fixture."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path not in {"/v1/hooks/inbound", "/v1/hooks/voice"}:
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

        event = request.get("event") or request.get("args", {}).get("event")
        if event == "FETCH_RECORD" and request.get("record_id") == "error-case":
            response = {
                "success": False,
                "error": {"message": "fixture requested a business-error response"},
            }
        elif event == "FETCH_RECORD":
            response = {
                "success": True,
                "data": {
                    "event": event,
                    "record_id": request.get("record_id"),
                    "source": "local-fixture",
                },
            }
        elif event == "LIST_ENTRIES":
            now = datetime.now(timezone.utc)
            past = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
            future = (now + timedelta(days=2)).isoformat().replace("+00:00", "Z")
            response = {
                "success": True,
                "entries": [
                    {"type": "specialistIntake", "state": "confirmed", "start": past},
                    {"type": "generalIntake", "state": "booked", "start": future},
                    {"type": "followup", "state": "scheduled", "start": future},
                    {"type": "followup", "state": "completed", "start": past},
                ],
            }
        elif event == "FIND_OPENINGS":
            args = request.get("args", {})
            windows = args.get("windows", [])
            if not windows:
                self.send_error(400)
                return
            window_start = datetime.fromisoformat(
                windows[0]["start"].replace("Z", "+00:00")
            )
            window_end = datetime.fromisoformat(
                windows[0]["end"].replace("Z", "+00:00")
            )
            response = {
                "openings": [
                    {
                        "id": "inside-window",
                        "start": (window_start + timedelta(hours=1))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    },
                    {
                        "id": "outside-window",
                        "start": (window_end + timedelta(hours=1))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    },
                ]
            }
        else:
            self.send_error(400)
            return
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
