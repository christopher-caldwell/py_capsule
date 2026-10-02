"""Small caller-owned runtime used by the fetch-record fixture wrapper."""

from __future__ import annotations

import sys
from typing import Any


class SessionAPI:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def log_event(self, message: Any) -> None:
        rendered = str(message)
        self._events.append(rendered)
        print(rendered, file=sys.stderr)


class FixtureRuntime:
    def __init__(self, context: dict[str, Any]) -> None:
        self.events = list(context.get("events", []))
        self.session = SessionAPI(self.events)

    def globals(self) -> dict[str, Any]:
        return {"Session": self.session}

    def export(self) -> dict[str, Any]:
        return {"events": self.events}
