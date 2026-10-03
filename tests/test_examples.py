from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
EXAMPLES = REPOSITORY / "examples" / "fixture-project" / "capsules"


def test_canonical_tool_sources_match_the_supplied_bodies() -> None:
    expected = {
        "fetch_record/fetch_record.py": "b7c42fb3a0d893933050035ce47211b863cb59bebf5db921ac42774390990976",
        "find_openings/find_openings.py": "0de34905f155e972e61205c8cfd50747aeeec3b703e3e6b651af9033c5331a9a",
        "list_entries/list_entries.py": "5ff60f68d287f0ae6ca0d79dec06aafaddf1c47bcdfcbe80cd6163ed1e5966a7",
        "handoff_specialist/handoff_specialist.py": "c71b8e9dcb9c5aa4f5e52d1317c88e3be3d13f47a2bf17995301d007fd389d1d",
    }
    for relative_path, digest in expected.items():
        source = (EXAMPLES / relative_path).read_bytes()
        assert source.endswith(b"\n")
        assert b"\r" not in source
        assert hashlib.sha256(source).hexdigest() == digest


def test_documented_canonical_examples_run_with_local_fixtures() -> None:
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(REPOSITORY / "examples" / "wrapper"),
            "python",
            str(REPOSITORY / "examples" / "run_canonical_examples.py"),
        ],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(completed.stdout)
    assert set(report) == {
        "example_one",
        "example_one_business_error",
        "example_two",
        "example_three",
        "example_five",
    }
    assert report["example_five"]["runtime_export"]["conversation"][
        "dialed_numbers"
    ] == ["+15551234567"]
