from __future__ import annotations

import inspect
import json
import keyword
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from ._config import (
    CapsuleConfigError,
    CapsuleIdentity,
    CapsuleConfig,
    _validate_json_value,
    effective_globals,
    effective_inputs,
    effective_log_level,
    load_capsule,
    read_capsule_manifest,
    validate_runtime_mapping,
)


class CapsuleExecutionError(RuntimeError):
    """A capsule could not be executed; ``run_dir`` points to retained evidence."""

    def __init__(
        self,
        message: str,
        run_dir: Path,
        *,
        cause: BaseException | None = None,
        runtime_export: Any = None,
        has_runtime_export: bool = False,
        runtime_export_error: dict[str, str] | None = None,
        phase: str | None = None,
    ):
        self.run_dir = run_dir
        self.cause = cause
        self.runtime_export = runtime_export
        self.has_runtime_export = has_runtime_export
        self.runtime_export_error = runtime_export_error
        self.phase = phase
        super().__init__(f"{message} (run evidence: {run_dir})")


@dataclass(frozen=True)
class CapsuleResult:
    """A successful capsule return value and its persisted run directory."""

    value: Any
    run_dir: Path
    runtime_export: Any = None
    has_runtime_export: bool = False

    def print_json(self, file: TextIO | None = None) -> None:
        """Write only the return value as one strict JSON value and a newline."""
        target = file if file is not None else sys.stdout
        target.write(
            json.dumps(
                self.value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
        target.write("\n")


def _write_json(path: Path, value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _read_capture(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _read_error(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return {
        "type": str(value.get("type", "CapsuleExecutionError")),
        "message": str(value.get("message", "capsule execution failed")),
        "traceback": str(value.get("traceback", "")),
        "phase": str(value.get("phase", "capsule_execution")),
        "runtime_export_error": value.get("runtime_export_error"),
    }


def _recognized_uv_errors(stdout: str, stderr: str) -> str:
    error_prefix = re.compile(r"^\s*(?:error:|×|caused by:)", re.IGNORECASE)
    lines = [
        line
        for output in (stdout, stderr)
        for line in output.splitlines()
        if error_prefix.match(line)
    ]
    return "\n".join(lines)


def _failure_detail(
    error: dict[str, Any] | None,
    runner_stdout: str,
    runner_stderr: str,
    returncode: int,
) -> str:
    if error is not None:
        phase = error.get("phase")
        prefix = {
            "runtime_import": "runtime import failed",
            "runtime_resolve": "runtime reference resolution failed",
            "runtime_construct": "runtime construction failed",
            "runtime_globals": "runtime globals failed",
            "runtime_export": "runtime export failed",
        }.get(phase)
        message = error["message"]
        if prefix:
            message = f"{prefix}: {message}"
        export_error = error.get("runtime_export_error")
        if isinstance(export_error, dict):
            message += (
                "; runtime export also failed: "
                f"{export_error.get('type', 'Exception')}: "
                f"{export_error.get('message', '')}"
            )
        if error["message"]:
            return f"{error['type']}: {message}"
        return error["traceback"] or "capsule execution failed"
    recognized = _recognized_uv_errors(runner_stdout, runner_stderr)
    return recognized or f"uv child exited with status {returncode}"


def _append_section(stream: TextIO, title: str, content: str) -> None:
    if not content:
        return
    stream.write(f"=== {title} ===\n")
    stream.write(content)
    if not content.endswith("\n"):
        stream.write("\n")


def _append_log(
    path: Path,
    *,
    runner_stdout: str = "",
    runner_stderr: str = "",
    snippet_stdout: str = "",
    snippet_stderr: str = "",
    error_detail: str = "",
    runtime_export_error: dict[str, str] | None = None,
    runner_failure: str = "",
) -> None:
    with path.open("a", encoding="utf-8") as stream:
        _append_section(stream, "uv stdout", runner_stdout)
        _append_section(stream, "uv stderr", runner_stderr)
        _append_section(stream, "snippet stdout", snippet_stdout)
        _append_section(stream, "snippet stderr", snippet_stderr)
        _append_section(stream, "capsule execution error", error_detail)
        if runtime_export_error:
            detail = (
                f"{runtime_export_error.get('type', 'Exception')}: "
                f"{runtime_export_error.get('message', '')}\n"
                f"{runtime_export_error.get('traceback', '')}"
            )
            _append_section(stream, "runtime export error", detail)
        _append_section(stream, "uv execution error", runner_failure)


def _write_terminal(stderr: str, label: str, content: str) -> None:
    if not content:
        return
    stderr.write(f"[py_capsule {label}]\n{content}")
    if not content.endswith("\n"):
        stderr.write("\n")


def _mirror_logs(
    level: str,
    *,
    runner_stdout: str = "",
    runner_stderr: str = "",
    snippet_stdout: str = "",
    snippet_stderr: str = "",
    error_detail: str = "",
    runner_failure: str = "",
) -> None:
    if level == "none":
        return
    if level == "error":
        diagnostic = error_detail or runner_failure
        _write_terminal(sys.stderr, "error", diagnostic)
        return

    _write_terminal(sys.stderr, "stdout", snippet_stdout)
    _write_terminal(sys.stderr, "stderr", snippet_stderr)
    _write_terminal(sys.stderr, "error", error_detail or runner_failure)
    if level == "debug":
        _write_terminal(sys.stderr, "uv stdout", runner_stdout)
        _write_terminal(sys.stderr, "uv stderr", runner_stderr)


def _make_run_dir(name: str) -> Path:
    root = Path.home() / ".py_capsule" / name / "runs"
    root.mkdir(parents=True, exist_ok=True)
    for _ in range(10):
        candidate = root / uuid.uuid4().hex
        try:
            candidate.mkdir(mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise OSError(f"could not allocate a unique run directory under {root}")


def _record(
    path: Path,
    *,
    identity: CapsuleIdentity,
    config: CapsuleConfig | None,
    run_id: str,
    started_at: str,
    status: str,
    finished_at: str | None = None,
    error_type: str | None = None,
) -> None:
    tool_path = config.tool_path if config is not None else identity.tool_path
    project_dir = config.project_dir if config is not None else identity.project_dir
    _write_json(
        path,
        {
            "run_id": run_id,
            "name": identity.name,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "capsule_dir": str(identity.capsule_dir),
            "tool_path": str(tool_path) if tool_path is not None else None,
            "project_dir": str(project_dir) if project_dir is not None else None,
            "error_type": error_type,
        },
    )


def _runtime_reference(runtime: str | type | Callable[..., Any] | None) -> str | None:
    if runtime is None:
        return None
    if isinstance(runtime, str):
        reference = runtime
    elif inspect.isclass(runtime) or inspect.isfunction(runtime):
        module = getattr(runtime, "__module__", "")
        qualified_name = getattr(runtime, "__qualname__", "")
        if (
            not module
            or module == "__main__"
            or not qualified_name
            or "<locals>" in qualified_name
        ):
            raise CapsuleConfigError(
                "runtime must be an importable class/function or a 'module:attribute' reference"
            )
        reference = f"{module}:{qualified_name}"
    else:
        raise CapsuleConfigError(
            "runtime must be an importable class/function or a 'module:attribute' reference"
        )
    if reference.count(":") != 1:
        raise CapsuleConfigError("runtime reference must use the 'module:attribute' form")
    module, attribute = reference.split(":", 1)
    parts = module.split(".") + attribute.split(".")
    if module == "__main__" or not module or not attribute or any(
        not part.isidentifier() or keyword.iskeyword(part) for part in parts
    ):
        raise CapsuleConfigError("runtime reference must use the 'module:attribute' form")
    return reference


def run(
    capsule_dir: str | Path,
    inputs: Mapping[str, Any] | None = None,
    globals: Mapping[str, Any] | None = None,
    log_level: str | None = None,
    runtime: str | type | Callable[..., Any] | None = None,
    runtime_context: Mapping[str, Any] | None = None,
) -> CapsuleResult:
    """Execute ``capsule.toml``'s function-body tool in its selected uv project.

    Project defaults, capsule values and runtime overrides are merged by key in
    that order. ``runtime`` may name an importable factory as
    ``"module:attribute"`` or be an importable class/function. The factory is
    resolved and called in the selected child project with ``runtime_context``;
    its result must provide synchronous ``globals()`` and ``export()`` methods.
    See README.md for details.
    """
    identity, manifest_data = read_capsule_manifest(capsule_dir)
    run_dir = _make_run_dir(identity.name)
    run_id = run_dir.name
    run_log = run_dir / "run.log"
    metadata_path = run_dir / "run.json"
    result_path = run_dir / "result.json"
    capture_paths = {
        "stdout": run_dir / ".child.stdout",
        "stderr": run_dir / ".child.stderr",
        "error": run_dir / ".execution-error.json",
        "runtime_export": run_dir / ".runtime-export.json",
    }
    started = datetime.now(timezone.utc).isoformat()
    config: CapsuleConfig | None = None
    selected_level = "none"
    runner_stdout = ""
    runner_stderr = ""
    snippet_stdout = ""
    snippet_stderr = ""
    error: dict[str, Any] | None = None
    runtime_export: Any = None
    has_runtime_export = False
    runtime_export_error: dict[str, str] | None = None
    captured_written = False
    execution_context_started = False
    try:
        _record(
            metadata_path,
            identity=identity,
            config=None,
            run_id=run_id,
            started_at=started,
            status="running",
        )
        config = load_capsule(identity, manifest_data)
        _record(
            metadata_path,
            identity=identity,
            config=config,
            run_id=run_id,
            started_at=started,
            status="running",
        )
        selected_level = effective_log_level(config, log_level)
        runtime_inputs = validate_runtime_mapping(inputs, "inputs")
        runtime_globals = validate_runtime_mapping(globals, "globals")
        runtime_ref = _runtime_reference(runtime)
        if runtime_ref is None and runtime_context is not None:
            raise CapsuleConfigError("runtime_context requires runtime")
        if runtime_context is None:
            child_runtime_context: dict[str, Any] = {}
        elif not isinstance(runtime_context, Mapping):
            raise CapsuleConfigError("runtime_context must be a mapping")
        else:
            child_runtime_context = dict(runtime_context)
            _validate_json_value(child_runtime_context, "runtime_context")
        final_inputs = effective_inputs(config, runtime_inputs)
        for name, value in final_inputs.items():
            if not isinstance(name, str) or not name.isidentifier():
                raise CapsuleConfigError(f"input name {name!r} must be a Python identifier")
            _validate_json_value(value, f"inputs.{name}")
        effective_global_names = (
            set(config.project_defaults.literal_globals)
            | set(config.project_defaults.env)
            | set(config.capsule_settings.literal_globals)
            | set(config.capsule_settings.env)
            | set(runtime_globals)
        )
        collisions = set(final_inputs) & effective_global_names
        if collisions:
            collision = sorted(collisions)[0]
            raise CapsuleConfigError(
                f"injection name {collision!r} is present in both effective inputs and globals"
            )
        execution_context_started = True
        resolved_globals = effective_globals(config, runtime_globals)
        request = {
            "project_dir": str(config.project_dir),
            "tool_path": str(config.tool_path),
            "inputs": final_inputs,
            "globals": resolved_globals,
            "runtime": (
                {"reference": runtime_ref, "context": child_runtime_context}
                if runtime_ref is not None
                else None
            ),
        }
        uv = shutil.which("uv")
        if uv is None:
            raise FileNotFoundError("uv executable not found on PATH")
        command = [
            uv,
            "run",
            "--project",
            str(config.project_dir),
            "--",
            "python",
            str(Path(__file__).with_name("_worker.py")),
            str(result_path),
            str(capture_paths["stdout"]),
            str(capture_paths["stderr"]),
            str(capture_paths["error"]),
            str(capture_paths["runtime_export"]),
        ]
        completed = subprocess.run(
            command,
            cwd=config.project_dir,
            input=json.dumps(request, ensure_ascii=False, allow_nan=False),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        runner_stdout, runner_stderr = completed.stdout, completed.stderr
        snippet_stdout = _read_capture(capture_paths["stdout"])
        snippet_stderr = _read_capture(capture_paths["stderr"])
        error = _read_error(capture_paths["error"])
        if capture_paths["runtime_export"].is_file():
            runtime_export = json.loads(
                capture_paths["runtime_export"].read_text(encoding="utf-8")
            )
            has_runtime_export = True
            _write_json(run_dir / "runtime-export.json", runtime_export)
        if error is not None and isinstance(error.get("runtime_export_error"), dict):
            runtime_export_error = {
                key: str(error["runtime_export_error"].get(key, ""))
                for key in ("type", "message", "traceback")
            }
        error_detail = (
            f"execution phase: {error['phase']}\n{error['traceback']}"
            if error is not None
            else ""
        )
        runner_failure = ""
        if completed.returncode != 0 and error is None:
            runner_failure = _failure_detail(
                None, runner_stdout, runner_stderr, completed.returncode
            )
        _append_log(
            run_log,
            runner_stdout=runner_stdout,
            runner_stderr=runner_stderr,
            snippet_stdout=snippet_stdout,
            snippet_stderr=snippet_stderr,
            error_detail=error_detail,
            runtime_export_error=runtime_export_error,
            runner_failure=runner_failure,
        )
        captured_written = True

        if completed.returncode != 0:
            _record(
                metadata_path,
                identity=identity,
                config=config,
                run_id=run_id,
                started_at=started,
                finished_at=datetime.now(timezone.utc).isoformat(),
                status="failed",
                error_type=error["type"] if error is not None else "CapsuleExecutionError",
            )
            _mirror_logs(
                selected_level,
                runner_stdout=runner_stdout,
                runner_stderr=runner_stderr,
                snippet_stdout=snippet_stdout,
                snippet_stderr=snippet_stderr,
                error_detail=error_detail,
                runner_failure=runner_failure,
            )
            raise CapsuleExecutionError(
                _failure_detail(error, runner_stdout, runner_stderr, completed.returncode),
                run_dir,
                runtime_export=runtime_export,
                has_runtime_export=has_runtime_export,
                runtime_export_error=runtime_export_error,
                phase=error.get("phase") if error is not None else None,
            )
        if not result_path.is_file():
            raise RuntimeError("child exited successfully without writing a return value")
        value = json.loads(result_path.read_text(encoding="utf-8"))
        _record(
            metadata_path,
            identity=identity,
            config=config,
            run_id=run_id,
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
            status="succeeded",
        )
        _mirror_logs(
            selected_level,
            runner_stdout=runner_stdout,
            runner_stderr=runner_stderr,
            snippet_stdout=snippet_stdout,
            snippet_stderr=snippet_stderr,
        )
        if runtime_ref is not None and not has_runtime_export:
            raise RuntimeError("child exited successfully without writing runtime export")
        return CapsuleResult(
            value=value,
            run_dir=run_dir,
            runtime_export=runtime_export,
            has_runtime_export=has_runtime_export,
        )
    except CapsuleExecutionError:
        raise
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if not captured_written:
            _append_log(
                run_log,
                runner_stdout=runner_stdout,
                runner_stderr=runner_stderr,
                snippet_stdout=snippet_stdout,
                snippet_stderr=snippet_stderr,
                error_detail=detail,
            )
        else:
            _append_log(run_log, error_detail=detail)
        _record(
            metadata_path,
            identity=identity,
            config=config,
            run_id=run_id,
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
            status="failed",
            error_type=type(exc).__name__,
        )
        _mirror_logs(
            selected_level,
            runner_stdout=runner_stdout,
            runner_stderr=runner_stderr,
            snippet_stdout=snippet_stdout,
            snippet_stderr=snippet_stderr,
            error_detail=detail,
        )
        if isinstance(exc, CapsuleConfigError):
            if execution_context_started:
                raise CapsuleExecutionError(str(exc), run_dir, cause=exc) from exc
            raise CapsuleConfigError(exc.message, run_dir=run_dir) from exc
        raise CapsuleExecutionError(f"capsule execution failed: {exc}", run_dir, cause=exc) from exc
    finally:
        for path in capture_paths.values():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
