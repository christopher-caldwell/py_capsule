"""Target-project bootstrap. This file runs inside the selected uv environment."""

from __future__ import annotations

import ast
import json
import math
import os
import sys
import tokenize
import traceback
from pathlib import Path
from typing import Any


_FUNCTION_NAME = "__py_capsule_execute__"


def _redirect_child_streams(stdout_path: Path, stderr_path: Path) -> None:
    for path, fd in ((stdout_path, 1), (stderr_path, 2)):
        stream = path.open("wb")
        os.dup2(stream.fileno(), fd)
        stream.close()


def _record_event(events_path: Path, message: Any) -> None:
    record = {"level": "info", "source": "Session.log_event", "message": str(message)}
    with events_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _validate_result(value: Any, location: str = "$") -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if math.isfinite(value):
            return
        raise TypeError(f"return value at {location} is not a finite JSON number")
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_result(item, f"{location}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(
                    f"return object key at {location} has unsupported type "
                    f"{type(key).__name__}; JSON object keys must be strings"
                )
            _validate_result(item, f"{location}.{key}")
        return
    raise TypeError(
        f"return value at {location} has unsupported type {type(value).__name__}; "
        "use JSON-compatible values"
    )


def _compile_function(source: str, filename: str, input_names: list[str]) -> Any:
    source_module = ast.parse(source, filename=filename, mode="exec")
    parameters = ", ".join(input_names)
    wrapper = ast.parse(f"def {_FUNCTION_NAME}({parameters}):\n    pass\n", filename)
    function = wrapper.body[0]
    function.body = source_module.body or [ast.Pass()]
    wrapper.type_ignores = source_module.type_ignores
    ast.fix_missing_locations(wrapper)
    return compile(wrapper, filename, "exec")


def _write_error(path: Path, exc: BaseException) -> None:
    error = {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
    }
    path.write_text(json.dumps(error, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 6:
        print("py_capsule worker expected five output file paths", file=sys.stderr)
        return 2
    result_path, stdout_path, stderr_path, events_path, error_path = map(Path, sys.argv[1:])
    try:
        _redirect_child_streams(stdout_path, stderr_path)
        request = json.load(sys.stdin)
        project_dir = Path(request["project_dir"])
        tool_path = Path(request["tool_path"])
        with tokenize.open(tool_path) as stream:
            source = stream.read()
        inputs: dict[str, Any] = request["inputs"]
        injected_globals: dict[str, Any] = request["globals"]

        # Python puts the script directory at sys.path[0]. Restore the selected
        # project as the first import root so project-owned helpers resolve.
        sys.path.insert(0, str(project_dir))

        class Session:
            @staticmethod
            def log_event(message: Any) -> None:
                _record_event(events_path, message)

        namespace: dict[str, Any] = {
            "__builtins__": __builtins__,
            "__file__": str(tool_path),
            "__name__": "__py_capsule_tool__",
        }
        namespace.update(injected_globals)
        namespace.setdefault("Session", Session)
        code = _compile_function(source, str(tool_path), list(inputs))
        exec(code, namespace, namespace)
        function = namespace.pop(_FUNCTION_NAME)
        value = function(**inputs)
        _validate_result(value)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        result_path.write_text(encoded, encoding="utf-8")
        return 0
    except BaseException as exc:
        _write_error(error_path, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
