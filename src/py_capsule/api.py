from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TextIO

from ._config import (
    CapsuleConfigError,
    _validate_json_value,
    effective_globals,
    load_capsule,
    validate_runtime_mapping,
)


class CapsuleExecutionError(RuntimeError):
    """A capsule could not be executed; ``run_dir`` points to retained evidence."""

    def __init__(self, message: str, run_dir: Path, *, cause: BaseException | None = None):
        self.run_dir = run_dir
        self.cause = cause
        super().__init__(f"{message} (run evidence: {run_dir})")


@dataclass(frozen=True)
class CapsuleResult:
    """A successful capsule return value and its persisted run directory."""

    value: Any
    run_dir: Path

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


def _append_log(path: Path, stdout: str, stderr: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        if stdout:
            stream.write("=== child stdout ===\n")
            stream.write(stdout)
            if not stdout.endswith("\n"):
                stream.write("\n")
        if stderr:
            stream.write("=== child stderr ===\n")
            stream.write(stderr)
            if not stderr.endswith("\n"):
                stream.write("\n")


def _mirror_logs(level: str, stdout: str, stderr: str, *, failed: bool) -> None:
    if level == "none":
        return
    if level == "error":
        if failed and stderr:
            sys.stderr.write(stderr)
            if not stderr.endswith("\n"):
                sys.stderr.write("\n")
        return
    for label, content in (("stdout", stdout), ("stderr", stderr)):
        if content:
            sys.stderr.write(f"[py_capsule {label}]\n{content}")
            if not content.endswith("\n"):
                sys.stderr.write("\n")


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
    config: Any,
    run_id: str,
    started_at: str,
    status: str,
    finished_at: str | None = None,
    error_type: str | None = None,
) -> None:
    _write_json(
        path,
        {
            "run_id": run_id,
            "name": config.name,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "capsule_dir": str(config.capsule_dir),
            "tool_path": str(config.tool_path),
            "project_dir": str(config.project_dir),
            "error_type": error_type,
        },
    )


def run(
    capsule_dir: str | Path,
    inputs: Mapping[str, Any] | None = None,
    globals: Mapping[str, Any] | None = None,
    log_level: str | None = None,
) -> CapsuleResult:
    """Execute ``capsule.toml``'s function-body tool in its selected uv project.

    Runtime inputs and globals replace capsule values by key. Values transferred
    to the child must be JSON-compatible. See README.md for manifest details.
    """
    config = load_capsule(capsule_dir)
    runtime_inputs = validate_runtime_mapping(inputs, "inputs")
    runtime_globals = validate_runtime_mapping(globals, "globals")
    selected_level = log_level if log_level is not None else config.configured_log_level
    selected_level = selected_level if selected_level is not None else "none"
    if not isinstance(selected_level, str) or selected_level not in {
        "none",
        "error",
        "info",
        "debug",
    }:
        raise CapsuleConfigError("log_level must be one of none, error, info, debug")

    effective_inputs = dict(config.inputs)
    effective_inputs.update(runtime_inputs)
    for name, value in effective_inputs.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise CapsuleConfigError(f"input name {name!r} must be a Python identifier")
        # Manifest names were validated; this also validates values inherited there.
        _validate_json_value(value, f"inputs.{name}")

    run_dir = _make_run_dir(config.name)
    run_id = run_dir.name
    run_log = run_dir / "run.log"
    metadata_path = run_dir / "run.json"
    result_path = run_dir / "result.json"
    started = datetime.now(timezone.utc).isoformat()
    _record(
        metadata_path,
        config=config,
        run_id=run_id,
        started_at=started,
        status="running",
    )

    stdout = ""
    stderr = ""
    try:
        resolved_globals = effective_globals(config, runtime_globals)
        request = {
            "project_dir": str(config.project_dir),
            "tool_path": str(config.tool_path),
            "inputs": effective_inputs,
            "globals": resolved_globals,
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
        stdout, stderr = completed.stdout, completed.stderr
        _append_log(run_log, stdout, stderr)
        _mirror_logs(selected_level, stdout, stderr, failed=completed.returncode != 0)
        if completed.returncode != 0:
            _record(
                metadata_path,
                config=config,
                run_id=run_id,
                started_at=started,
                finished_at=datetime.now(timezone.utc).isoformat(),
                status="failed",
                error_type="CapsuleExecutionError",
            )
            detail = stderr.strip() or f"uv child exited with status {completed.returncode}"
            raise CapsuleExecutionError(detail, run_dir)
        if not result_path.is_file():
            raise RuntimeError("child exited successfully without writing a return value")
        value = json.loads(result_path.read_text(encoding="utf-8"))
        _record(
            metadata_path,
            config=config,
            run_id=run_id,
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
            status="succeeded",
        )
        return CapsuleResult(value=value, run_dir=run_dir)
    except CapsuleExecutionError:
        raise
    except Exception as exc:
        if not run_log.exists():
            run_log.touch()
        with run_log.open("a", encoding="utf-8") as stream:
            stream.write(f"=== py_capsule failure ===\n{type(exc).__name__}: {exc}\n")
        _mirror_logs(selected_level, stdout, stderr, failed=True)
        _record(
            metadata_path,
            config=config,
            run_id=run_id,
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
            status="failed",
            error_type=type(exc).__name__,
        )
        if isinstance(exc, CapsuleConfigError):
            if selected_level == "error":
                sys.stderr.write(f"py_capsule: {exc}\n")
            raise CapsuleExecutionError(str(exc), run_dir, cause=exc) from exc
        if selected_level == "error" and not stderr:
            sys.stderr.write(f"py_capsule: {type(exc).__name__}: {exc}\n")
        raise CapsuleExecutionError(f"capsule execution failed: {exc}", run_dir, cause=exc) from exc
