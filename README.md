# py_capsule

`py_capsule` runs a trusted Python function body in a selected `uv` project and
returns its top-level `return` value to a Python caller. The runner supplies
JSON-compatible inputs and globals, and can construct a target-available runtime
inside the selected child project to provide live Python globals. It does not
sandbox the snippet: filesystem, network, environment and subprocess access
remain available to the code.

The first release is **0.0.1 (alpha)**. The API is experimental and may change
in any `0.x` release, including patches. Pin the exact version in applications
and review changes before upgrading; `0.0.1` does not promise a stable API.

## Development

Install Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and [just](https://just.systems/man/en/packages.html), then run:

```sh
just                 # list commands
just format          # apply formatting
just check           # format check, lint, types, tests, canonical examples
just build           # wheel/sdist metadata and installed-wheel acceptance
```

Ruff, ty, pytest, and Twine are managed by uv; recipes sync their locked versions.
The supplied capsule bodies are immutable fixtures, excluded from static tooling
and verified by hashes and execution tests. All other Python files are checked.

For maintainers, `just publish patch` (or `minor` / `major`) prepares the version
commit, annotated tag, and GitHub Release. GitHub Actions publishes to PyPI with
Trusted Publishing. Use `just publish-initial` for the first **0.0.1** release.
See [releasing](docs/releasing.md) for setup, prerequisites, and recovery.

## Install

PyCapsule requires Python 3.11 or newer and the `uv` executable on `PATH`.
Install the package in the caller or wrapper environment; capsules themselves
execute inside their selected `uv` project.

For a published release:

```sh
uv add py-capsule==0.0.1
# or
python -m pip install py-capsule==0.0.1
```

Until the first PyPI release is published, a development checkout can be wired
into a wrapper project explicitly:

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

Run the wrapper with `uv run --project /path/to/wrapper-project /path/to/wrapper-project/wrapper.py`.
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
only the returned JSON value there. Captured snippet output is retained under
`~/.py_capsule/<name>/runs/<run-id>/`; the result exposes that directory as
`result.run_dir`. Capsule manifests may define `[inputs]`, `[globals]`, and `[env]`.
Call-time inputs and globals override capsule values, which override
`[tool.py_capsule]` defaults from the selected project. `[env]` maps an
injected global name to a host environment variable name. The optional
`project` setting selects the `uv` project; without it, PyCapsule finds the
nearest ancestor `pyproject.toml` from the capsule directory. Log levels are
`none`, `error`, `info`, and `debug`.

## Child-local runtime globals

The caller may name a runtime factory that is importable from the selected
project. PyCapsule imports and constructs it in the `uv` child, passing a
JSON-compatible context value. The returned object provides synchronous
`globals()` and `export()` methods:

```python
result = run(
    capsule,
    runtime="host_runtime:build_runtime",
    runtime_context={"conversation_id": "example-123"},
)
print(result.runtime_export)
```

The selected project's `host_runtime.py` can define the factory and runtime
objects. Context may be an object with string keys, list, string, finite number,
boolean, or JSON null. Omitted context remains `{}`; explicit
`runtime_context=None` passes JSON null. The factory defines its own schema.
`globals()` returns a mapping of live Python objects; `export()`
returns JSON-compatible state. Runtime globals cannot reuse an input or JSON
global name. Each call builds a fresh runtime.

A tool body uses supplied globals directly; it does not import them or know
about PyCapsule. For example, an unchanged tool may contain:

```python
Session.set_value("offered_openings", matched)
Conversation.set_metadata("handoff_done", True)
```

The selected runtime can satisfy those names with:

```python
class HostRuntime:
    def __init__(self, context):
        self.session = SessionShim(context)
        self.conversation = ConversationShim(context)

    def globals(self):
        return {
            "Session": self.session,
            "Conversation": self.conversation,
        }

    def export(self):
        return {"state": "..."}
```

`Session` and `Conversation` are examples, not built-in PyCapsule concepts.
If a tool references a name that its selected runtime does not provide, normal
Python name resolution fails.

The export is also written to `runtime-export.json` in the run directory and
can be passed into a later call as context. A successful pre-failure export is
available as `CapsuleExecutionError.runtime_export`. Use
`has_runtime_export` to distinguish a successful JSON-null export from no
export. Caller-owned runtime code must be
importable in the selected child project. Use the `module:attribute` reference
as the primary API: it avoids importing target-only dependencies in the wrapper.
An importable class/function object is convenience syntax for the same child
reference; it does not carry code, parent state, or the wrapper's import paths.

For a separate wrapper and target project, put the adapter module in the target
project or declare an adapter package as a target dependency (a local uv path
dependency works too). An adapter available only in the wrapper source tree or
environment cannot be reconstructed by either reference form. Normal usage
needs no caller-written `sys.path` changes or import-root calculations; PyCapsule
uses the target project and its declared dependencies for both runtime and tool
imports.

The repository wrapper uses the same public path with
`runtime="fixture_runtime:FixtureRuntime"`. That runtime lives in the selected
fixture project and is imported and built by its `uv` child.

## Run the controlled example

The repository includes the supplied function-body shape, a project-owned
`lib.helpers`, a `requests` target dependency, a separate wrapper environment,
and a local HTTP fixture. The helper and service are illustrative and do not
claim to reproduce proprietary Decagon behavior.

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
business-error return. Set `PYCAPSULE_LOG_LEVEL=info` to mirror captured
snippet output and runtime messages on stderr after the snippet finishes, while
preserving the JSON stdout. `info` and `debug` do not stream live output while a
snippet is running.
The fixture URL defaults to `http://127.0.0.1:8765` and can be changed with
`PY_CAPSULE_FIXTURE_URL`.

## Run the four canonical examples

The fixture project includes the four supplied capsule bodies unchanged:
`fetch_record`, `find_openings`, `list_entries`, and `handoff_specialist`. Their
ordinary `requests` and `lib.helpers` imports run in the target project's uv
environment. The example runner starts a temporary local HTTP fixture, provides
fake credentials and verifies each returned value plus the exported Session or
Conversation effects. No external service or proprietary host runtime is used.

From the repository root, run:

```sh
uv run --project examples/wrapper python examples/run_canonical_examples.py
```

It prints a JSON report for all four examples and also checks the fetch-record
business-error path. It binds the local fixture to an available loopback port,
then shuts it down when the runs finish.
