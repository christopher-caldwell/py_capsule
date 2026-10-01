# py_capsule

`py_capsule` runs a trusted Python function body in a selected `uv` project and
returns its top-level `return` value to a Python caller. The runner supplies
inputs, globals and the small `Session.log_event()` helper used by the supplied
example. It does not sandbox the snippet: filesystem, network, environment and
subprocess access remain available to the code.

## Install for a wrapper

The executable wrapper imports `py_capsule` in its own environment. For a local
checkout, a small wrapper project can depend on the source tree directly:

```toml
# wrapper-project/pyproject.toml
[project]
name = "capsule-wrapper"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["py-capsule"]

[tool.uv.sources]
py-capsule = { path = "../py_capsule", editable = true }
```

Run the wrapper with `uv run --project /path/to/wrapper-project wrapper.py`.
An existing wrapper environment can instead install the checkout with
`uv pip install -e /path/to/py_capsule`. The snippet project does not need
`py_capsule` as a dependency; it does need its own normal `pyproject.toml` and
dependencies for the snippet imports.

## First call

A capsule directory contains `capsule.toml` and a selected Python source file.
The source file is executed as a function body, so top-level `return` works and
the file itself is left unchanged.

```toml
# capsules/fetch_record/capsule.toml
name = "fetch_record"
tool = "./fetch_record.py"
# Optional. Without this, the nearest ancestor pyproject.toml is used.
# project = "../.."

[inputs]
record_id = "example-record"

[globals]
use_test_env = true
variant = "default"
```

```python
from pathlib import Path
from py_capsule import run

capsule = Path(__file__).resolve().parent / "capsules" / "fetch_record"
result = run(capsule, inputs={"record_id": "runtime-record"})
result.print_json()
```

`run()` is quiet on caller stdout. `result.print_json()` deliberately writes
only the returned JSON value there. Captured snippet output and `Session` events
are retained under `~/.py_capsule/<name>/runs/<run-id>/`; the result exposes that
directory as `result.run_dir`. See [docs/configuration.md](docs/configuration.md)
for the manifest, project selection, environment globals, failure and logging
details.
