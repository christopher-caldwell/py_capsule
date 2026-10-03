"""Example-owned host globals for the canonical capsules.

These classes demonstrate one way for a caller runtime to provide live globals.
They are illustrative fixtures, not PyCapsule APIs or proprietary host behavior.
"""

from __future__ import annotations

import sys
from typing import Any


class SessionAPI:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    def log_event(self, message: Any, details: Any = None) -> None:
        self._state["events"].append({"message": str(message), "details": details})
        print(str(message), file=sys.stderr)

    def set_value(self, name: str, value: Any) -> None:
        self._state["values"][name] = value

    def log_failure(self, name: str, details: Any) -> None:
        self._state["failures"].append({"name": name, "details": details})


class ConversationAPI:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    def set_external_id(self, value: str) -> None:
        self._state["external_id"] = value
        self._state["calls"].append(["set_external_id", value])

    def merge_metadata(self, values: dict[str, Any]) -> None:
        self._state["metadata"].update(values)
        self._state["calls"].append(["merge_metadata", values])

    def set_metadata(self, name: str, value: Any) -> None:
        self._state["metadata"][name] = value
        self._state["calls"].append(["set_metadata", name, value])

    def dial_number(self, number: str) -> None:
        self._state["dialed_numbers"].append(number)
        self._state["calls"].append(["dial_number", number])


class FixtureRuntime:
    """Create fresh live host-style objects from JSON-compatible context."""

    def __init__(self, context: dict[str, Any]) -> None:
        self.session_state = {
            "values": dict(context.get("session_values", {})),
            "events": [],
            "failures": [],
        }
        self.conversation_state = {
            "external_id": context.get("external_id"),
            "metadata": dict(context.get("metadata", {})),
            "dialed_numbers": [],
            "calls": [],
        }
        self.session = SessionAPI(self.session_state)
        self.conversation = ConversationAPI(self.conversation_state)

    def globals(self) -> dict[str, Any]:
        return {"Session": self.session, "Conversation": self.conversation}

    def export(self) -> dict[str, Any]:
        return {
            "session": self.session_state,
            "conversation": self.conversation_state,
        }
