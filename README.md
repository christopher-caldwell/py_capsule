# PyCapsule

PyCapsule runs a trusted Python function body inside a selected `uv` project and
returns its value to a Python caller. Use it when a snippet needs a project's
dependencies and local imports, or live objects supplied by that project.
The caller supplies inputs; PyCapsule launches the project's Python environment,
captures output, and retains evidence for each run.

## A small capsule

Start with a project containing `pyproject.toml`:

```toml
# greeting-project/pyproject.toml
[project]
name = "greeting-project"
version = "0.1.0"
requires-python = ">=3.11"
```

A capsule is a directory with a manifest and a Python source file:

```toml
# greeting-project/capsules/greet/capsule.toml
name = "greet"
tool = "greet.py"

[inputs]
name = "Morgan"

[globals]
GREETING = "Hello"
```

```python
# greeting-project/capsules/greet/greet.py
return {"message": f"{GREETING}, {name}!"}
```

Call it from an environment with `capsule-runner` installed:

```python
# demo.py, beside greeting-project/
from pathlib import Path
from py_capsule import run

capsule = Path(__file__).resolve().parent / "greeting-project" / "capsules" / "greet"
result = run(capsule, inputs={"name": "Avery"})
result.print_json()  # {"message":"Hello, Avery!"}
```

`result.value` holds the returned Python value. `run()` is quiet on caller
stdout; `print_json()` writes only the returned JSON value and a newline.

## The selected project

PyCapsule finds the nearest ancestor `pyproject.toml` from the capsule directory.
Set `project` in the manifest to select another project explicitly; relative
paths resolve from the capsule directory. The selected project becomes the
child's working directory and import root, and `uv` supplies its dependencies.
It does not need `py_capsule` installed. The caller and capsule can use separate
environments.

`[inputs]` become function parameters. `[globals]` supply JSON values as Python
globals; `[env]` can supply globals from named host environment variables.
Call arguments override manifest values, which override `[tool.py_capsule]`
project defaults, by name.

The source executes as a function body, so ordinary local imports and a top
level `return` work without rewriting the file. For example, a project with
`lib/helpers.py` can use `from lib.helpers import ...` in its capsule. Inputs,
JSON globals, and returns must contain strings, booleans, finite numbers,
`None`, lists, or dictionaries with string keys.

Capsules are trusted code with filesystem, network, environment, and subprocess
access. PyCapsule does not sandbox them.

## Live runtime objects

For objects such as `Session` or `Conversation`, name a factory importable in
the selected project:

```python
result = run(
    capsule,
    runtime="host_runtime:Runtime",
    runtime_context={"state": {}},
)
print(result.runtime_export)
```

PyCapsule constructs the runtime in the child process. Its synchronous
`globals()` method supplies live objects, and `export()` returns JSON state.
For example, the selected project's `host_runtime.py` can contain:

```python
class SessionState:
    def __init__(self, state):
        self.state = dict(state)

    def set_value(self, key, value):
        self.state[key] = value


class Runtime:
    def __init__(self, context):
        self.session = SessionState(context.get("state", {}))

    def globals(self):
        return {"Session": self.session}

    def export(self):
        return {"state": self.session.state}
```

A capsule using this runtime can call `Session.set_value("seen", True)` directly.
`Session` and `Conversation` are caller defined names; PyCapsule supplies no
built in host APIs. Runtime globals cannot reuse an input or JSON global name.
Each call creates a fresh runtime; pass an export into a later call as context
to carry state forward. See the [runtime reference](docs/reference.md#runtime-injection)
for context validation, import requirements, and export behavior on failure.

## Installation

Requires Python 3.11 or newer and the `uv` executable on `PATH`. The distribution
is named `capsule-runner`; the Python import is `py_capsule`.

For a published release, install in the caller environment:

```sh
uv add capsule-runner==0.0.1
# or
python -m pip install capsule-runner==0.0.1
```

Version **0.0.1 is alpha**. The API may change in any `0.x` release, including
patches. Pin the exact version and review changes before upgrading.
For checkout development, an existing caller environment can install with
`uv pip install -e /path/to/py_capsule`; see the
[wrapper setup](docs/reference.md#executable-wrappers) for a separate uv project.

## Reference and examples

The [reference](docs/reference.md) covers manifests, precedence, runtime
injection, results, logs, errors, persisted evidence, and executable wrappers.
Each run is retained under `~/.py_capsule/<name>/runs/<run-id>/`, available through
`result.run_dir`. Output, returns, and runtime exports may contain sensitive data.

The [controlled examples](docs/reference.md#controlled-examples) exercise four
capsules against a local HTTP fixture with fake credentials and a project owned
runtime. Run them from the checkout with `just acceptance`.

## Development and releases

Install Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and [just](https://just.systems/man/en/packages.html), then run:

```sh
just                 # list commands
just format          # apply formatting
just acceptance      # check example locks and canonical capsule behavior
just check           # format check, lint, types, tests, acceptance
just build           # inspect packages, test installed wheel, rebuild sdist
```

Recipes use the locked Ruff, ty, pytest, and Twine versions. Supplied capsule
bodies are immutable fixtures, excluded from static tooling and verified by
hashes and execution tests. All other Python files are checked.

Maintainers use `just publish patch` (or `minor` / `major`) to prepare the version
commit, tag, and GitHub Release. `just publish-initial` releases the initial
0.0.1 version. GitHub Actions validates the artifacts and publishes them to PyPI.
See the [release guide](docs/maintainers/releasing.md) for setup and recovery.

PyCapsule is available under the [MIT license](LICENSE).
