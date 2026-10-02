"""Target-project bootstrap. This file runs inside the selected uv environment."""

from __future__ import annotations

import ast
import importlib
import json
import keyword
import math
import os
import sys
import tokenize
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast


_FUNCTION_NAME = "__py_capsule_execute__"
_RESERVED_RUNTIME_GLOBALS = {
    "__builtins__",
    "__file__",
    "__name__",
    _FUNCTION_NAME,
}


def _redirect_child_streams(stdout_path: Path, stderr_path: Path) -> None:
    for path, fd in ((stdout_path, 1), (stderr_path, 2)):
        stream = path.open("wb")
        os.dup2(stream.fileno(), fd)
        stream.close()


def _validate_result(value: Any, location: str = "$") -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if math.isfinite(value):
            return
        raise TypeError(f"value at {location} is not a finite JSON number")
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_result(item, f"{location}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(
                    f"object key at {location} has unsupported type "
                    f"{type(key).__name__}; JSON object keys must be strings"
                )
            _validate_result(item, f"{location}.{key}")
        return
    raise TypeError(
        f"value at {location} has unsupported type {type(value).__name__}; "
        "use JSON-compatible values"
    )


def _compile_function(source: str, filename: str, input_names: list[str]) -> Any:
    source_module = ast.parse(source, filename=filename, mode="exec")
    parameters = ", ".join(input_names)
    wrapper = ast.parse(f"def {_FUNCTION_NAME}({parameters}):\n    pass\n", filename)
    function = cast(ast.FunctionDef, wrapper.body[0])
    function.body = source_module.body or [ast.Pass()]
    wrapper.type_ignores = source_module.type_ignores
    ast.fix_missing_locations(wrapper)
    return compile(wrapper, filename, "exec")


def _error_record(exc: BaseException, phase: str) -> dict[str, str]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
        "phase": phase,
    }


def _runtime_globals(runtime: Any) -> dict[str, Any]:
    method = getattr(runtime, "globals", None)
    if not callable(method):
        raise TypeError("runtime must provide a callable globals() method")
    values = method()
    if not isinstance(values, Mapping):
        raise TypeError("runtime globals() must return a mapping")
    result = dict(values)
    for name in result:
        if (
            not isinstance(name, str)
            or not name.isidentifier()
            or keyword.iskeyword(name)
            or name in _RESERVED_RUNTIME_GLOBALS
        ):
            raise ValueError(f"runtime global name {name!r} is invalid or reserved")
    return result


def main() -> int:
    if len(sys.argv) != 6:
        print("py_capsule worker expected five output file paths", file=sys.stderr)
        return 2
    result_path, stdout_path, stderr_path, error_path, export_path = map(
        Path, sys.argv[1:]
    )
    try:
        _redirect_child_streams(stdout_path, stderr_path)
        request = json.load(sys.stdin)
        project_dir = Path(request["project_dir"])
        tool_path = Path(request["tool_path"])
        with tokenize.open(tool_path) as stream:
            source = stream.read()
        inputs: dict[str, Any] = request["inputs"]
        injected_globals: dict[str, Any] = request["globals"]

        # Compile before runtime construction to avoid setup side effects for
        # invalid tool source. Python puts this worker's directory at sys.path[0].
        code = _compile_function(source, str(tool_path), list(inputs))
        sys.path.insert(0, str(project_dir))

        namespace: dict[str, Any] = {
            "__builtins__": __builtins__,
            "__file__": str(tool_path),
            "__name__": "__py_capsule_tool__",
        }
        namespace.update(injected_globals)
        runtime_spec = request.get("runtime")
        runtime = None
        phase = "runtime_import"
        if runtime_spec is not None:
            module_name, attribute_path = runtime_spec["reference"].split(":", 1)
            try:
                module = importlib.import_module(module_name)
            except BaseException as exc:
                raise RuntimeError(
                    f"cannot import runtime module {module_name!r} in selected target "
                    f"project {str(project_dir)!r}; the runtime and its dependencies "
                    f"must be available there: {exc}"
                ) from exc
            phase = "runtime_resolve"
            factory: Any = module
            try:
                for part in attribute_path.split("."):
                    factory = getattr(factory, part)
            except BaseException as exc:
                raise RuntimeError(
                    f"cannot resolve runtime attribute {attribute_path!r} "
                    f"from {module_name!r}: {exc}"
                ) from exc
            phase = "runtime_construct"
            runtime = factory(runtime_spec["context"])
            phase = "runtime_globals"
            runtime_globals = _runtime_globals(runtime)
            collisions = set(inputs) & set(runtime_globals)
            collisions |= set(injected_globals) & set(runtime_globals)
            if collisions:
                name = sorted(collisions)[0]
                raise ValueError(
                    f"runtime global {name!r} collides with an input or configured global"
                )
            namespace.update(runtime_globals)

        phase = "capsule_execution"
        exec(code, namespace, namespace)
        function = namespace.pop(_FUNCTION_NAME)
        value = function(**inputs)

        phase = "result_validation"
        _validate_result(value)
        encoded_result = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except BaseException as exc:
        failure: dict[str, Any] = _error_record(
            exc, phase if "phase" in locals() else "worker_setup"
        )
        runtime = locals().get("runtime")
        if runtime is not None:
            try:
                export_method = getattr(runtime, "export", None)
                if not callable(export_method):
                    raise TypeError("runtime must provide a callable export() method")
                exported = export_method()
                _validate_result(exported, "$runtime_export")
                encoded_export = json.dumps(
                    exported,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
                export_path.write_text(encoded_export, encoding="utf-8")
            except BaseException as export_exc:
                failure["runtime_export_error"] = _error_record(
                    export_exc, "runtime_export"
                )
        error_path.write_text(json.dumps(failure, ensure_ascii=False), encoding="utf-8")
        return 1

    if runtime is not None:
        try:
            export_method = getattr(runtime, "export", None)
            if not callable(export_method):
                raise TypeError("runtime must provide a callable export() method")
            exported = export_method()
            _validate_result(exported, "$runtime_export")
            encoded_export = json.dumps(
                exported,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            export_path.write_text(encoded_export, encoding="utf-8")
        except BaseException as exc:
            error_path.write_text(
                json.dumps(_error_record(exc, "runtime_export"), ensure_ascii=False),
                encoding="utf-8",
            )
            return 1
    result_path.write_text(encoded_result, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
