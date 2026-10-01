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

[globals]
variant = "default"

[env]
TEST_SERVICE_API_KEY = "LOCAL_TEST_SERVICE_API_KEY"
```

`inputs` become function parameters. `globals` and `env` supply Python globals;
`env` maps an injected name to the name of a variable in the host process. The
runner resolves only the winning environment mapping and never copies the whole
host environment. One manifest cannot define a global destination in both
`[globals]` and `[env]`. Duplicate TOML keys are rejected by the TOML parser.

Runtime mappings passed to `run(capsule_dir, inputs=..., globals=...,
log_level=...)` replace configured values by name, including `False`, `0`, empty
strings, empty containers and `None`. A replaced environment reference is not
looked up. Values crossing into the target process must be JSON-compatible:
strings, booleans, finite numbers, `None`, lists and dictionaries with string
keys. No arbitrary Python objects are transferred.

The built-in `Session` provides only `Session.log_event(message)`. Supplying a
global named `Session` replaces this fallback. This is a local compatibility
shim, not full Decagon runtime emulation.

## Results, logs and errors

`run()` returns a `CapsuleResult` with `value` and `run_dir`. It does not print
the return value. `result.print_json()` writes one strict JSON value and a
newline to caller stdout; a returned string remains a JSON string. Unsupported
return values, including non-finite floats, fail clearly.

The child process's stdout and stderr, including `uv` diagnostics and
`Session.log_event` messages, are captured in `run.log`. Each run also has a
`run.json` provenance record; a successful JSON-compatible return is in
`result.json`. Ordinary execution failures raise `CapsuleExecutionError`, whose
`run_dir` points to the retained evidence. Manifest and runtime mapping
validation errors raise `CapsuleConfigError`; execution-time configuration and
child failures are wrapped in `CapsuleExecutionError`. A returned error-shaped
business value remains an ordinary successful value.

Log levels are `none`, `error`, `info` and `debug`; the default is `none`.
Retention is independent of terminal display. `none` never mirrors logs.
`error` mirrors captured stderr on failed execution; `info` and `debug` mirror
captured child output to caller stderr. The caller's stdout is not replaced.
User wrapper prints and shell stream merging are outside this guarantee.

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
the stage's `CONTROL_TOWER_WORKSPACE` explicitly. The library does not integrate
with Control Tower or interpret stdout as workflow state.
