from __future__ import annotations

import os


def resolve_base_url(*, use_test_env: object, variant: object) -> str:
    """Resolve the local example service; this is not the proprietary helper."""
    if str(use_test_env).lower() != "true":
        raise RuntimeError("the fixture project only provides a local test endpoint")
    if variant not in {"default", "fixture"}:
        raise ValueError(f"unsupported fixture variant: {variant!r}")
    return os.environ.get(
        "PY_CAPSULE_FIXTURE_URL", "http://127.0.0.1:8765"
    ).rstrip("/")
