# Capsule configuration and execution

## Manifest

Each capsule is a directory containing `capsule.toml`. The manifest requires a
safe single-component `name` and an explicit `tool` path. Tool and project paths
are resolved from the capsule directory, independent of the caller's current
directory. The optional `project` selects a directory containing
`pyproject.toml`; without it, the runner finds the nearest ancestor project
starting at the capsule directory. The selected project is both the snippet's
working directory and its shared-import root.

```toml
name = "fetch_record"
tool = "./fetch_record.py"
project = "../.."

[inputs]
record_id = "example-record"
use_test_env = true
variant = "default"

[env]
TEST_SERVICE_API_KEY = "LOCAL_TEST_SERVICE_API_KEY"
```

`inputs` become function parameters. `globals` and `env` supply Python globals;
`env` maps an injected name to the name of a variable in the host process. The
runner resolves only the winning environment mapping and never copies the whole
host environment. Shared defaults can live in the selected project's
`pyproject.toml`:

```toml
[tool.py_capsule]
log_level = "none"

[tool.py_capsule.inputs]
use_test_env = true
variant = "default"

[tool.py_capsule.env]
TEST_SERVICE_API_KEY = "LOCAL_TEST_SERVICE_API_KEY"
```

The full precedence is runtime > capsule > selected project > built-in defaults.
Each mapping merges by name, and nested payloads replace lower-layer values as
a whole. Runtime presence wins for `false`, `0`, `""`, empty containers and
`null`. Inputs and globals merge independently, then are checked for a final
name collision. A literal global and an environment-backed global compete for
the same destination; the higher layer wins before environment lookup, so an
overridden missing lower-layer variable is harmless. Every layer is validated
on its own before merging. One config file cannot define a global destination
in both `[globals]` and `[env]`. Input names cannot also be defined as global
names in the same file, and the final effective input/global namespaces cannot
overlap. The global name `__py_capsule_execute__` is reserved for the runner.
Duplicate TOML keys are rejected by the TOML parser. Project defaults come only
from the selected project and are not recursively inherited from its parent
projects; the `[tool.py_capsule]` section is optional.

Runtime mappings passed to `run(capsule_dir, inputs=..., globals=...,
log_level=...)` replace configured values by name, including `False`, `0`, empty
strings, empty containers and `None`. A replaced environment reference is not
looked up. Values crossing into the target process must be JSON-compatible:
strings, booleans, finite numbers, `None`, lists and dictionaries with string
keys. No arbitrary Python objects are transferred from the caller process.

Pass `runtime="module:attribute"` and optional `runtime_context={...}` to
construct child-local live globals. The selected project's import root is added
in the child, so callers do not need to calculate it or edit `sys.path`. The
reference can also be an importable class or function object; PyCapsule converts
it to its module and qualified name and resolves it in the child. The factory
receives the context mapping as its sole positional argument and returns an
object with synchronous `globals()` and `export()` methods. The globals method
must return a mapping of valid Python names to live objects. Runtime globals
cannot collide with capsule inputs or JSON globals. PyCapsule supplies no
host-specific names such as `Session` or `Conversation`.

Each call constructs a fresh runtime. Its `export()` result must be JSON
compatible and is available as `CapsuleResult.runtime_export`; it is also
persisted separately as `runtime-export.json`, not copied into `run.json`.
After a normal Python execution failure, the runner still attempts export and
exposes a successful value as `CapsuleExecutionError.runtime_export`. If both
the capsule and export fail, the capsule failure remains primary and the
secondary export failure is retained in the exception and run log.

## Results, logs and errors

`run()` returns a `CapsuleResult` with `value` and `run_dir`. It does not print
the return value. `result.print_json()` writes one strict JSON value and a
newline to caller stdout; a returned string remains a JSON string. Unsupported
return values, including non-finite floats, fail clearly. Return validation
rejects unsupported shapes before writing `result.json`; object keys must
already be strings and are never coerced.

Snippet stdout/stderr and `uv` diagnostics are captured in `run.log`. Each run
also has a `run.json` provenance record; a successful JSON-compatible return is
in `result.json`. Ordinary execution failures raise `CapsuleExecutionError`,
whose `run_dir` points to the retained evidence. Before a valid capsule name can be read, manifest failures raise
`CapsuleConfigError` without creating a run. After the name is known, manifest,
runtime mapping and merge validation failures create a failed run with
diagnostics and raise `CapsuleConfigError` with `run_dir` set to that evidence.
Missing effective environment values and child execution failures raise
`CapsuleExecutionError`, also with `run_dir`. A returned error-shaped business
value remains an ordinary successful value.

Log levels are `none`, `error`, `info` and `debug`; the default is `none`.
Retention is independent of terminal display. `none` never mirrors logs.
`error` mirrors structured capsule failures or recognized `uv` error
diagnostics; it does not mirror unstructured snippet output or `uv` progress
just because they appeared on stderr. `info` mirrors snippet streams and
execution failures to caller stderr after the child process exits. `debug`
also mirrors `uv` launcher diagnostics after execution. Neither level streams
output while a snippet is running, so a still-running or hung snippet provides
no live terminal progress. The caller stdout is not replaced. User wrapper
prints and shell stream merging are outside this guarantee.

Run directories use the shared capsule name namespace under
`~/.py_capsule/<name>/runs/<unique-id>/`. Same-named capsules intentionally share
that history, while `run.json` records the source paths. Raw captured output and
returned values may contain sensitive data. py_capsule does not redact or encrypt
them; it avoids writing resolved environment values or the complete host
environment into its provenance record.

## Executable wrappers

Install py_capsule into the wrapper's environment. For a local checkout, a
wrapper project's `pyproject.toml` can declare `py-capsule` and point
`[tool.uv.sources]` at the checkout; the README shows the complete snippet and
launch command. An existing wrapper environment can use
`uv pip install -e /path/to/py_capsule`. A user-owned `uv run --script` wrapper
has its own environment, so py_capsule must be declared or installed there.
`uv run --project <project> python ...` separately selects the target project
for the snippet. `uv` handles that project's ordinary environment and lock
behavior. py_capsule requires `uv` on `PATH` and has no plain-Python fallback.

For a Control Tower executable, anchor the capsule to the wrapper file or use
the stage's `CONTROL_TOWER_WORKSPACE` explicitly:

```python
import os
from pathlib import Path
from py_capsule import run

workspace = Path(os.environ["CONTROL_TOWER_WORKSPACE"])
result = run(workspace / "capsules" / "fetch_record")
result.print_json()
```

The library does not integrate with Control Tower or interpret stdout as
workflow state.

The repository example uses a separate wrapper project with a local editable
dependency on this checkout. It does not assume a public package release or
that installing a uv tool makes the library importable in every environment.
Its shell entry point finds the checkout path before invoking `uv`, and its
Python wrapper anchors the capsule relative to `__file__`. A caller-owned
Control Tower stage can use the same pattern and explicitly locate its
workspace. Printed JSON is captured stdout; Control Tower does not automatically
make it shared next-stage state.
