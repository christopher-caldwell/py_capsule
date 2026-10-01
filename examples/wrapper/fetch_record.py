#!/usr/bin/env python3
"""Caller-owned wrapper; the fixture project supplies requests and lib.helpers."""

from __future__ import annotations

import os
from pathlib import Path

from py_capsule import run


examples_dir = Path(__file__).resolve().parents[1]
capsule_dir = examples_dir / "fixture-project" / "capsules" / "fetch_record"
result = run(
    capsule_dir,
    inputs={"record_id": os.environ.get("RECORD_ID", "example-record")},
    log_level=os.environ.get("PYCAPSULE_LOG_LEVEL"),
)
result.print_json()
