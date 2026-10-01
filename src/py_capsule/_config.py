from __future__ import annotations

import keyword
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_LOG_LEVELS = {"none", "error", "info", "debug"}


class CapsuleConfigError(ValueError):
    """The capsule manifest or supplied runtime configuration is invalid."""


@dataclass(frozen=True)
class CapsuleConfig:
    name: str
    capsule_dir: Path
    tool_path: Path
    project_dir: Path
    inputs: dict[str, Any]
    literal_globals: dict[str, Any]
    env: dict[str, str]
    configured_log_level: str | None


def _mapping(value: Any, label: str, source: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CapsuleConfigError(f"{source}: [{label}] must be a TOML table")
    return value


def _validate_json_value(value: Any, label: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise CapsuleConfigError(f"{label} must not contain NaN or Infinity")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CapsuleConfigError(f"{label} object keys must be strings")
            _validate_json_value(item, f"{label}.{key}")
        return
    raise CapsuleConfigError(
        f"{label} has unsupported value type {type(value).__name__}; "
        "use JSON-compatible values"
    )


def _validate_name(name: Any, source: Path) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name in {".", ".."}:
        raise CapsuleConfigError(
            f"{source}: name must be a nonempty single path component "
            "containing only letters, digits, '.', '_' or '-'"
        )
    return name


def _validate_injection_names(values: Mapping[str, Any], label: str, source: Path) -> None:
    for name in values:
        if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
            raise CapsuleConfigError(
                f"{source}: {label} destination {name!r} must be a Python identifier"
            )


def _project_directory(capsule_dir: Path, selection: Any, source: Path) -> Path:
    if selection is not None:
        if not isinstance(selection, str) or not selection:
            raise CapsuleConfigError(f"{source}: project must be a nonempty path string")
        project = (capsule_dir / selection).resolve()
        if project.is_file():
            if project.name != "pyproject.toml":
                raise CapsuleConfigError(
                    f"{source}: explicit project file must be named pyproject.toml"
                )
            project = project.parent
        pyproject = project / "pyproject.toml"
        if not pyproject.is_file():
            raise CapsuleConfigError(
                f"{source}: selected project {project} has no pyproject.toml"
            )
        return project

    for candidate in (capsule_dir, *capsule_dir.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise CapsuleConfigError(
        f"{source}: no containing pyproject.toml found; set project in capsule.toml"
    )


def load_capsule(capsule_dir: str | Path) -> CapsuleConfig:
    capsule = Path(capsule_dir).expanduser().resolve()
    if not capsule.is_dir():
        raise CapsuleConfigError(f"capsule directory does not exist: {capsule}")
    manifest = capsule / "capsule.toml"
    if not manifest.is_file():
        raise CapsuleConfigError(f"capsule manifest does not exist: {manifest}")
    try:
        with manifest.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CapsuleConfigError(f"cannot read {manifest}: {exc}") from exc

    name = _validate_name(data.get("name"), manifest)
    tool = data.get("tool")
    if not isinstance(tool, str) or not tool:
        raise CapsuleConfigError(f"{manifest}: tool must be a nonempty path string")
    tool_path = (capsule / tool).resolve()
    if not tool_path.is_file():
        raise CapsuleConfigError(f"{manifest}: tool file does not exist: {tool_path}")
    project_dir = _project_directory(capsule, data.get("project"), manifest)

    inputs = _mapping(data.get("inputs", {}), "inputs", manifest)
    literal_globals = _mapping(data.get("globals", {}), "globals", manifest)
    env = _mapping(data.get("env", {}), "env", manifest)
    _validate_injection_names(inputs, "inputs", manifest)
    _validate_injection_names(literal_globals, "globals", manifest)
    _validate_injection_names(env, "env", manifest)
    duplicate_destinations = literal_globals.keys() & env.keys()
    if duplicate_destinations:
        duplicate = sorted(duplicate_destinations)[0]
        raise CapsuleConfigError(
            f"{manifest}: global {duplicate!r} is defined in both [globals] and [env]"
        )
    for key, value in inputs.items():
        _validate_json_value(value, f"{manifest} [inputs].{key}")
    for key, value in literal_globals.items():
        _validate_json_value(value, f"{manifest} [globals].{key}")
    for key, value in env.items():
        if not isinstance(value, str) or not value:
            raise CapsuleConfigError(
                f"{manifest}: [env].{key} must name a nonempty environment variable"
            )

    log_level = data.get("log_level")
    if log_level is not None and (not isinstance(log_level, str) or log_level not in _LOG_LEVELS):
        raise CapsuleConfigError(
            f"{manifest}: log_level must be one of {', '.join(sorted(_LOG_LEVELS))}"
        )
    return CapsuleConfig(
        name=name,
        capsule_dir=capsule,
        tool_path=tool_path,
        project_dir=project_dir,
        inputs=dict(inputs),
        literal_globals=dict(literal_globals),
        env=dict(env),
        configured_log_level=log_level,
    )


def validate_runtime_mapping(value: Mapping[str, Any] | None, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CapsuleConfigError(f"{label} must be a mapping")
    result = dict(value)
    for name, item in result.items():
        if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
            raise CapsuleConfigError(f"{label} key {name!r} must be a Python identifier")
        _validate_json_value(item, f"{label}.{name}")
    return result


def effective_globals(config: CapsuleConfig, runtime: dict[str, Any]) -> dict[str, Any]:
    # Select a source by destination before looking up environment values. An
    # overridden env reference therefore cannot fail because it is unset.
    selected: dict[str, tuple[str, Any]] = {
        key: ("literal", value) for key, value in config.literal_globals.items()
    }
    selected.update({key: ("env", value) for key, value in config.env.items()})
    selected.update({key: ("literal", value) for key, value in runtime.items()})
    resolved: dict[str, Any] = {}
    import os

    for destination, (kind, source) in selected.items():
        if kind == "literal":
            resolved[destination] = source
        else:
            if source not in os.environ:
                raise CapsuleConfigError(
                    f"environment variable {source!r} for global {destination!r} is not set"
                )
            resolved[destination] = os.environ[source]
    return resolved
