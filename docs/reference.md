# PyCapsule reference

For a first capsule and call, see the [README](../README.md).

- [Calling `run()`](#calling-run)
- [Manifest and project defaults](#manifest-and-project-defaults)
- [Runtime injection](#runtime-injection)
- [Results](#results)
- [Errors](#errors)
- [Logs](#logs)
- [Persisted run evidence](#persisted-run-evidence)
- [Executable wrappers](#executable-wrappers)
- [Controlled examples](#controlled-examples)

## Calling `run()`

```python
from py_capsule import run

result = run(capsule_dir)
```

`capsule_dir` accepts a string or `Path`. Omit optional arguments to use defaults.

| Argument | Meaning | Default |
| --- | --- | --- |
| `inputs` | JSON values supplied as function parameters | Configured inputs |
| `globals` | JSON values supplied as Python globals | Configured globals |
| `log_level` | Terminal log display | Configured level, otherwise `"none"` |
| `runtime` | Factory reference or importable class/function | No runtime |
| `runtime_context` | One JSON value passed to the factory | Fresh `{}` when a runtime is supplied |

`inputs` and `globals` accept mappings or `None`. With no runtime, omit
`runtime_context` or pass `None`; other explicit context values require a runtime.

The capsule source executes as a synchronous function body with inputs as
parameters, ordinary Python imports, and a top level `return`. Falling through
returns `None`. The source file is not rewritten. Imports resolve against the selected project's root and
installed dependencies; the selected project is also the working directory.

Capsules are trusted code. They retain filesystem, network, environment, and
subprocess access; PyCapsule provides no sandbox. The selected project does not
need `py_capsule` installed: the caller launches its worker through `uv`.

## Manifest and project defaults

Each capsule is a directory containing `capsule.toml`. The manifest requires a
nonempty `name` containing only letters, digits, `.`, `_`, or `-` (except `.`
and `..`) and an explicit `tool` path. Tool and project paths
are resolved from the capsule directory, independent of the caller's current
directory. The optional `project` selects a directory containing
`pyproject.toml`, or the file itself. Without it, the runner finds the nearest
ancestor project starting at the capsule directory. The selected project is both the snippet's
working directory and its import root.

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
runner resolves only the winning `[env]` mapping when deciding which values to
inject as Python globals. The `uv` child also inherits the caller's ordinary
subprocess environment; `[env]` does not isolate it. Shared
defaults can live in the selected project's `pyproject.toml`:

```toml
[tool.py_capsule]
log_level = "none"

[tool.py_capsule.inputs]
use_test_env = true
variant = "default"

[tool.py_capsule.env]
TEST_SERVICE_API_KEY = "LOCAL_TEST_SERVICE_API_KEY"
```

The full precedence for configured inputs/globals is call argument override >
capsule > selected project > built in defaults. Each mapping merges by name,
and nested payloads replace lower layer values as a whole. Call argument presence
wins for `false`, `0`, `""`, empty containers and `null`. Inputs and globals
merge independently, then are checked for a final name collision. A literal
global and a global supplied by the environment compete for the same
destination. The higher layer wins before environment lookup, so an overridden
missing lower layer variable is harmless. Every layer is validated
on its own before merging. One config file cannot define a global destination
in both `[globals]` and `[env]`. Input names cannot also be defined as global
names in the same file, and the final effective input/global namespaces cannot
overlap. The global name `__py_capsule_execute__` is reserved for the runner.
Duplicate TOML keys are rejected by the TOML parser. Project defaults come only
from the selected project and are not recursively inherited from its parent
projects; the `[tool.py_capsule]` section is optional.

Values crossing into the target process must be compatible with JSON: strings,
booleans, finite numbers, `None`, lists, and dictionaries with string keys.
No arbitrary Python objects are transferred from the caller process.

## Runtime injection

Pass `runtime="module:attribute"` and optional `runtime_context={...}` to
construct child local live globals. The selected project's import root is added
in the child, so callers do not need to calculate it or edit `sys.path`. The
reference can also be an importable class or function object; PyCapsule converts
it to its module and qualified name and resolves it in the child. Both forms
require that module and its dependencies to be available in the target project.
The callable form does not transfer code, parent mutations, or wrapper import
paths. Use a project owned module or a declared target dependency, including a
local uv path dependency for a shared adapter package. The string form avoids
having to import target project dependencies in the wrapper. PyCapsule does not
automatically expose the wrapper source tree or environment to child imports.

The factory receives one context value compatible with JSON as its sole
positional argument: an object with string keys, list, string, finite number, boolean, or
JSON null. Omission passes a fresh `{}` for compatibility; explicit
`runtime_context=None` passes JSON null. Context is validated and transported
as one value, never merged into inputs/globals; the factory defines its schema.
Top level Mapping implementations are also accepted as JSON objects. The factory
returns an object with synchronous `globals()` and `export()` methods.
`globals()` must return a mapping of valid Python names to live objects.
Names `__builtins__`, `__file__`, `__name__`, and `__py_capsule_execute__` are
reserved for the worker.
Runtime globals cannot collide with capsule inputs or JSON globals. PyCapsule
supplies no host specific names such as `Session` or `Conversation`.

Tool bodies use names from the runtime directly and do not import them. A tool
that calls `Session.set_value(...)` or `Conversation.set_metadata(...)` works
when the selected runtime provides those names. If it does not, ordinary Python
name resolution fails; PyCapsule does not synthesize missing host APIs.

Each call constructs a fresh runtime. Its `export()` result must be JSON
compatible and is available as `CapsuleResult.runtime_export`; it is also
persisted separately as `runtime-export.json`, not copied into `run.json`.
After a normal Python execution failure, the runner still attempts export and
exposes a successful value as `CapsuleExecutionError.runtime_export`. If both
the capsule and export fail, the capsule failure remains primary and the
secondary export failure is retained in the exception and run log.
`has_runtime_export` distinguishes a successful JSON null export from absent
output. Failure side export requires a caught Python failure and a working
exporter; hard process termination cannot guarantee a final snapshot. Runtime
exports, returns, logs, and error messages may contain sensitive data; context
is not persisted as a separate artifact and runtime export is deliberately
retained.

## Results

`run()` returns a `CapsuleResult` with `value`, `run_dir`,
`runtime_export`, and `has_runtime_export`. Without a runtime,
`runtime_export` is `None` and `has_runtime_export` is false. It does not
print the return value. `result.print_json()` writes one strict JSON value and a
newline to caller stdout; a returned string remains a JSON string. Unsupported
return values, including nonfinite floats, fail clearly. Return validation
rejects unsupported shapes before writing `result.json`; object keys must
already be strings and are never coerced.

## Errors

Ordinary execution failures raise `CapsuleExecutionError`, whose `run_dir`
points to the retained evidence. Before a valid capsule name can be read,
manifest failures raise `CapsuleConfigError` without creating a run. After the
name is known, manifest, call argument, and merge validation failures create a
failed run with diagnostics and raise `CapsuleConfigError` with `run_dir` set to that evidence.
Missing effective environment values and child execution failures raise
`CapsuleExecutionError`, also with `run_dir`. Runtime backed execution errors
also expose `runtime_export` and `has_runtime_export`; when export itself fails
after an earlier capsule failure, `runtime_export_error` retains that secondary
failure and the capsule failure remains primary. `phase` identifies the child
execution stage when available. A returned error shaped business value remains
an ordinary successful value.

## Logs

Log levels are `none`, `error`, `info` and `debug`; the default is `none`.
Retention is independent of terminal display. `none` never mirrors logs.
`error` mirrors structured capsule failures or recognized `uv` error
diagnostics; it does not mirror unstructured snippet output or `uv` progress
just because they appeared on stderr. `info` mirrors snippet streams and
execution failures to caller stderr after the child process exits. `debug`
also mirrors `uv` launcher diagnostics after execution. Neither level streams
output while a snippet is running, so a running or hung snippet provides
no live terminal progress. The caller stdout is not replaced. User wrapper
prints and shell stream merging are outside this guarantee.

## Persisted run evidence

Each run retains `run.log` with snippet stdout/stderr and `uv` diagnostics, plus
`run.json` with status, timestamps, source paths, and error type. A successful
return is stored in `result.json`; a successful runtime export is stored in
`runtime-export.json`. Retention does not depend on the log level.

Run directories use the shared capsule name namespace under
`~/.py_capsule/<name>/runs/<unique-id>/`. Capsules with the same name share that
history, while `run.json` records the source paths. Raw captured output and
returned values may contain sensitive data. py_capsule does not redact or encrypt
them; it avoids writing resolved environment values or the complete host
environment into its provenance record.

## Executable wrappers

Install `capsule-runner` into the wrapper's environment. For a local checkout,
a wrapper project can declare an editable source:

```toml
# wrapper-project/pyproject.toml
[project]
name = "capsule-wrapper"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["capsule-runner"]

[tool.uv.sources]
capsule-runner = { path = "../py_capsule", editable = true }
```

Launch the wrapper with:

```sh
uv run --project /path/to/wrapper-project /path/to/wrapper-project/wrapper.py
```

An existing wrapper environment can instead install the checkout with
`uv pip install -e /path/to/py_capsule`. A `uv run --script` wrapper uses its own
environment, so it also needs `capsule-runner` declared or installed there.

These launch commands select the wrapper environment. The capsule manifest
selects the target project, whose normal dependencies must satisfy the capsule's
imports. `uv` manages that project's environment and lock behavior. PyCapsule
requires `uv` on `PATH` and has no fallback when it is missing.

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
Python wrapper anchors the capsule relative to `__file__`. A caller owned
Control Tower stage can use the same pattern and explicitly locate its
workspace. Printed JSON is captured stdout; any transfer of state between stages
is the caller's responsibility.

## Controlled examples

The repository includes the supplied function body shape, a project owned
`lib.helpers`, a `requests` dependency, a separate wrapper environment,
and a local HTTP fixture. The helper and service are illustrative fixtures.

From the repository root, in one terminal, start the fixture:

```sh
uv run --project examples/fixture-project \
  examples/fixture-project/fixture_service.py
```

In another terminal, use fake fixture credentials and run the executable from
any working directory:

```sh
export PYCAPSULE_EXAMPLE_TEST_API_KEY=fixture-test-key
export PYCAPSULE_EXAMPLE_LIVE_API_KEY=fixture-live-key
cd /tmp
/path/to/py_capsule/examples/bin/fetch-record
```

The wrapper emits only the returned JSON value on stdout. Set `RECORD_ID` to
choose another record; `RECORD_ID=error-case` demonstrates an ordinary
business error return. Set `PYCAPSULE_LOG_LEVEL=info` to mirror captured
snippet output on stderr after the snippet finishes, while preserving JSON
stdout. `info` and `debug` do not stream live output while a
snippet is running.
The fixture URL defaults to `http://127.0.0.1:8765` and can be changed with
`PY_CAPSULE_FIXTURE_URL`.

### Acceptance runner

The fixture project includes the four supplied capsule bodies unchanged:
`fetch_record`, `find_openings`, `list_entries`, and `handoff_specialist`. Their
ordinary `requests` and `lib.helpers` imports run in the target project's uv
environment. The example runner starts a temporary local HTTP fixture, provides
fake credentials and verifies each returned value plus the exported Session or
Conversation effects. No external service or proprietary host runtime is used.

From the repository root, run:

```sh
just acceptance
```

It prints a JSON report for all four examples and also checks the `fetch_record`
business error path. It binds the local fixture to an available loopback port,
then shuts it down when the runs finish.
