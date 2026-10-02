#!/usr/bin/env python3
"""Run and verify the four unchanged canonical capsules against local fixtures."""

from __future__ import annotations

import importlib.util
import json
import os
import threading
from pathlib import Path
from typing import Any

from py_capsule import run


REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE_PROJECT = REPOSITORY / "examples" / "fixture-project"
RUNTIME = "fixture_runtime:FixtureRuntime"


def _load_fixture_service() -> Any:
    path = FIXTURE_PROJECT / "fixture_service.py"
    spec = importlib.util.spec_from_file_location("py_capsule_example_service", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load local fixture service at {path}")
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    return service


def _capsule(name: str) -> Path:
    return FIXTURE_PROJECT / "capsules" / name


def run_examples() -> dict[str, Any]:
    """Start an ephemeral local HTTP fixture, run every capsule, and assert effects."""
    service = _load_fixture_service()
    server = service.ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    fixture_env = {
        "PYCAPSULE_EXAMPLE_TEST_API_KEY": "fixture-test-key",
        "PYCAPSULE_EXAMPLE_LIVE_API_KEY": "fixture-live-key",
        "PY_CAPSULE_FIXTURE_URL": f"http://127.0.0.1:{server.server_port}",
    }
    previous_env = {name: os.environ.get(name) for name in fixture_env}
    os.environ.update(fixture_env)
    try:
        one = run(
            _capsule("fetch_record"),
            inputs={"record_id": "canonical-record-101"},
            runtime=RUNTIME,
        )
        assert one.value == {
            "event": "FETCH_RECORD",
            "record_id": "canonical-record-101",
            "source": "local-fixture",
        }
        assert one.runtime_export == {
            "session": {"values": {}, "events": [], "failures": []},
            "conversation": {
                "external_id": None,
                "metadata": {},
                "dialed_numbers": [],
                "calls": [],
            },
        }

        one_error = run(
            _capsule("fetch_record"),
            inputs={"record_id": "error-case"},
            runtime=RUNTIME,
        )
        assert one_error.value == "Something went wrong"
        assert one_error.runtime_export["session"]["events"] == [
            {"message": "fixture requested a business-error response", "details": None}
        ]

        two = run(
            _capsule("find_openings"),
            runtime=RUNTIME,
        )
        inside_window = {
            "id": "inside-window",
            "start": "2035-06-14T09:00:00Z",
        }
        assert two.value == {"openings": [inside_window]}
        assert two.runtime_export["session"]["values"] == {
            "window_empty": False,
            "none_found": False,
            "offered_openings": [inside_window],
            "requested_window": "morning",
            "requested_day": "2035-06-14",
        }
        two_events = two.runtime_export["session"]["events"]
        assert [event["message"] for event in two_events] == [
            "Find Openings Request Payload",
            "Find Openings API Response",
            "FIND_OPENINGS succeeded",
        ]
        assert two_events[-1]["details"] == {"opening_count": 2}

        three = run(
            _capsule("list_entries"),
            runtime=RUNTIME,
        )
        selected_entry = three.value[0]
        assert len(three.value) == 1
        assert selected_entry["type"] == "followup"
        assert selected_entry["state"] == "scheduled"
        assert three.runtime_export["session"]["values"] == {
            "entries": three.value,
            "entry_count": 1,
        }
        assert three.runtime_export["session"]["events"][0]["message"] == "API Response"

        five = run(_capsule("handoff_specialist"), runtime=RUNTIME)
        assert five.value == {
            "success": True,
            "message": "Transferring to specialist line",
        }
        conversation = five.runtime_export["conversation"]
        assert conversation["external_id"] == "app-voice-v2-outbound:+15550001111"
        assert conversation["metadata"] == {
            "contact_name": "Morgan Fixture",
            "summary_text": "Requests a specialist callback.",
            "goals": ["schedule consultation"],
            "involvement_level": "high",
            "status_note": "Caller confirmed details.",
            "is_away": False,
            "handoff_target": "specialist",
            "handoff_done": True,
        }
        assert conversation["dialed_numbers"] == ["+15551234567"]
        assert [call[0] for call in conversation["calls"]] == [
            "set_external_id",
            "merge_metadata",
            "set_metadata",
            "dial_number",
        ]

        return {
            "example_one": {"value": one.value, "runtime_export": one.runtime_export},
            "example_one_business_error": {
                "value": one_error.value,
                "runtime_export": one_error.runtime_export,
            },
            "example_two": {"value": two.value, "runtime_export": two.runtime_export},
            "example_three": {
                "value": three.value,
                "runtime_export": three.runtime_export,
            },
            "example_five": {"value": five.value, "runtime_export": five.runtime_export},
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        for name, previous in previous_env.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous


if __name__ == "__main__":
    print(json.dumps(run_examples(), ensure_ascii=False, separators=(",", ":")))
