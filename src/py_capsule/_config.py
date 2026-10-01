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
_RUNNER_GLOBAL_NAME = "__py_capsule_execute__"


class CapsuleConfigError(ValueError):
    """Configuration is invalid; ``run_dir`` is set when evidence was retained."""

    def __init__(self, message: str, run_dir: Path | None = None):
        self.message = message
        self.run_dir = run_dir
        detail = f"{message} (run evidence: {run_dir})" if run_dir is not None else message
        super().__init__(detail)


@dataclass(frozen=True)
class ConfigLayer:
    inputs: dict[str, Any]
    literal_globals: dict[str, Any]
    env: dict[str, str]
    log_level: str | None


@dataclass(frozen=True)
class CapsuleIdentity:
    name: str
    capsule_dir: Path
    manifest_path: Path


@dataclass(frozen=True)
class CapsuleConfig:
    name: str
    capsule_dir: Path
    tool_path: Path
    project_dir: Path
    project_defaults: ConfigLayer
    capsule_settings: ConfigLayer


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
        if label in {"globals", "env"} and name == _RUNNER_GLOBAL_NAME:
            raise CapsuleConfigError(
                f"{source}: global destination {name!r} is reserved by the capsule runner"
            )


def _load_layer(data: Mapping[str, Any], source: Path) -> ConfigLayer:
    inputs = _mapping(data.get("inputs", {}), "inputs", source)
    literal_globals = _mapping(data.get("globals", {}), "globals", source)
    env = _mapping(data.get("env", {}), "env", source)
    _validate_injection_names(inputs, "inputs", source)
    _validate_injection_names(literal_globals, "globals", source)
    _validate_injection_names(env, "env", source)
    duplicate_destinations = literal_globals.keys() & env.keys()
    if duplicate_destinations:
        duplicate = sorted(duplicate_destinations)[0]
        raise CapsuleConfigError(
            f"{source}: global {duplicate!r} is defined in both [globals] and [env]"
        )
    input_global_collisions = inputs.keys() & (literal_globals.keys() | env.keys())
    if input_global_collisions:
        collision = sorted(input_global_collisions)[0]
        raise CapsuleConfigError(
            f"{source}: injection name {collision!r} is defined in both [inputs] "
            "and a global source ([globals] or [env])"
        )
    for key, value in inputs.items():
        _validate_json_value(value, f"{source} [inputs].{key}")
    for key, value in literal_globals.items():
        _validate_json_value(value, f"{source} [globals].{key}")
    for key, value in env.items():
        if not isinstance(value, str) or not value:
            raise CapsuleConfigError(
                f"{source}: [env].{key} must name a nonempty environment variable"
            )

    log_level = data.get("log_level")
    if "log_level" in data and (
        not isinstance(log_level, str) or log_level not in _LOG_LEVELS
    ):
        raise CapsuleConfigError(
            f"{source}: log_level must be one of {', '.join(sorted(_LOG_LEVELS))}"
        )
    return ConfigLayer(
        inputs=dict(inputs),
        literal_globals=dict(literal_globals),
        env=dict(env),
        log_level=log_level,
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


def read_capsule_manifest(capsule_dir: str | Path) -> tuple[CapsuleIdentity, dict[str, Any]]:
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
    return CapsuleIdentity(name, capsule, manifest), data


def load_capsule(identity: CapsuleIdentity, data: Mapping[str, Any]) -> CapsuleConfig:
    capsule = identity.capsule_dir
    manifest = identity.manifest_path
    name = identity.name
    tool = data.get("tool")
    if not isinstance(tool, str) or not tool:
        raise CapsuleConfigError(f"{manifest}: tool must be a nonempty path string")
    tool_path = (capsule / tool).resolve()
    if not tool_path.is_file():
        raise CapsuleConfigError(f"{manifest}: tool file does not exist: {tool_path}")
    project_dir = _project_directory(capsule, data.get("project"), manifest)

    capsule_settings = _load_layer(data, manifest)
    project_file = project_dir / "pyproject.toml"
    try:
        with project_file.open("rb") as stream:
            project_data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CapsuleConfigError(f"cannot read {project_file}: {exc}") from exc
    tool_section = project_data.get("tool", {})
    if not isinstance(tool_section, dict):
        raise CapsuleConfigError(f"{project_file}: [tool] must be a TOML table")
    py_capsule_section = tool_section.get("py_capsule", {})
    if not isinstance(py_capsule_section, dict):
        raise CapsuleConfigError(f"{project_file}: [tool.py_capsule] must be a TOML table")
    project_defaults = _load_layer(py_capsule_section, project_file)
    return CapsuleConfig(
        name=name,
        capsule_dir=capsule,
        tool_path=tool_path,
        project_dir=project_dir,
        project_defaults=project_defaults,
        capsule_settings=capsule_settings,
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
        if label == "globals" and name == _RUNNER_GLOBAL_NAME:
            raise CapsuleConfigError(
                f"global name {name!r} is reserved by the capsule runner"
            )
        _validate_json_value(item, f"{label}.{name}")
    return result


def effective_inputs(config: CapsuleConfig, runtime: dict[str, Any]) -> dict[str, Any]:
    values = dict(config.project_defaults.inputs)
    values.update(config.capsule_settings.inputs)
    values.update(runtime)
    return values


def effective_log_level(config: CapsuleConfig, runtime: str | None) -> str:
    selected = (
        runtime
        if runtime is not None
        else config.capsule_settings.log_level
        if config.capsule_settings.log_level is not None
        else config.project_defaults.log_level
    )
    selected = "none" if selected is None else selected
    if not isinstance(selected, str) or selected not in _LOG_LEVELS:
        raise CapsuleConfigError("log_level must be one of none, error, info, debug")
    return selected


def effective_globals(config: CapsuleConfig, runtime: dict[str, Any]) -> dict[str, Any]:
    # Select a source by destination before looking up environment values. An
    # overridden env reference therefore cannot fail because it is unset.
    selected: dict[str, tuple[str, Any]] = {
        key: ("literal", value)
        for key, value in config.project_defaults.literal_globals.items()
    }
    selected.update(
        {key: ("env", value) for key, value in config.project_defaults.env.items()}
    )
    selected.update(
        {
            key: ("literal", value)
            for key, value in config.capsule_settings.literal_globals.items()
        }
    )
    selected.update(
        {key: ("env", value) for key, value in config.capsule_settings.env.items()}
    )
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
