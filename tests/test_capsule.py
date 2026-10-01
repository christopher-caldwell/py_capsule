from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from py_capsule import CapsuleConfigError, CapsuleExecutionError, run


REPOSITORY = Path(__file__).resolve().parents[1]


def make_project(tmp_path: Path, *, name: str = "fixture") -> tuple[Path, Path]:
    project = tmp_path / name
    capsule = project / "capsules" / "sample"
    capsule.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture-project"\nversion = "0.0.1"\n',
        encoding="utf-8",
    )
    (capsule / "capsule.toml").write_text(
        'name = "sample"\ntool = "tool.py"\n[inputs]\nflag = true\n',
        encoding="utf-8",
    )
    return project, capsule


def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_runs_unchanged_function_body_in_project_and_retains_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolated_home(monkeypatch, tmp_path)
    project, capsule = make_project(tmp_path)
    (project / "fixture_helper.py").write_text("VALUE = 'project import'\n", encoding="utf-8")
    source = (
        "from fixture_helper import VALUE\n"
        "flag = not flag\n"
        "print('snippet stdout')\n"
        "print('snippet stderr', file=__import__('sys').stderr)\n"
        "Session.log_event(VALUE)\n"
        "return {'flag': flag, 'value': VALUE, 'input': record_id}\n"
    )
    tool = capsule / "tool.py"
    tool.write_bytes(source.encode())

    result = run(capsule, inputs={"record_id": "runtime", "flag": False})

    assert result.value == {"flag": True, "value": "project import", "input": "runtime"}
    assert tool.read_bytes() == source.encode()
    assert result.run_dir.is_dir()
    assert json.loads((result.run_dir / "result.json").read_text()) == result.value
    metadata = json.loads((result.run_dir / "run.json").read_text())
    assert metadata["status"] == "succeeded"
    assert metadata["tool_path"] == str(tool.resolve())
    log = (result.run_dir / "run.log").read_text()
    assert "snippet stdout" in log
    assert "snippet stderr" in log
    assert "project import" in log
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_print_json_is_only_the_json_return_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "tool.py").write_text("print('not result')\nreturn 'true'\n", encoding="utf-8")
    result = run(capsule)
    result.print_json()
    assert capsys.readouterr().out == '"true"\n'


def test_multiline_string_contents_are_preserved_when_wrapping_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    source = "return '''first\nsecond'''\n"
    (capsule / "tool.py").write_text(source, encoding="utf-8")
    result = run(capsule)
    assert result.value == "first\nsecond"
    assert (capsule / "tool.py").read_text(encoding="utf-8") == source


def test_target_project_dependency_is_available_only_in_the_uv_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    project, capsule = make_project(tmp_path)
    import_name = "capsule_target_only_probe_xyz"
    assert importlib.util.find_spec(import_name) is None
    dep = project / "target_probe_dep"
    dep.mkdir()
    (dep / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['setuptools>=68']\nbuild-backend = 'setuptools.build_meta'\n"
        "[project]\nname = 'capsule-target-only-probe'\nversion = '0.0.1'\n",
        encoding="utf-8",
    )
    (dep / f"{import_name}.py").write_text("VALUE = 'target project dependency'\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture-project"\nversion = "0.0.1"\n'
        'dependencies = ["capsule-target-only-probe"]\n'
        '[tool.uv.sources]\ncapsule-target-only-probe = { path = "target_probe_dep", editable = true }\n',
        encoding="utf-8",
    )
    (capsule / "tool.py").write_text(
        f"import {import_name}\nreturn {import_name}.VALUE\n", encoding="utf-8"
    )
    result = run(capsule)
    assert result.value == "target project dependency"
    assert importlib.util.find_spec(import_name) is None


def test_session_fallback_and_json_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "tool.py").write_text("Session.log_event('event')\nreturn None\n", encoding="utf-8")
    result = run(capsule)
    assert result.value is None
    result.print_json()
    assert capsys.readouterr().out == "null\n"
    assert '"status": "succeeded"' in (result.run_dir / "run.json").read_text()
    assert "event" in (result.run_dir / "run.log").read_text()


def test_explicit_session_global_is_not_replaced_by_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "tool.py").write_text("Session.log_event('event')\nreturn True\n", encoding="utf-8")
    with pytest.raises(CapsuleExecutionError, match="NoneType"):
        run(capsule, globals={"Session": None})


def test_failure_is_raised_and_retained_but_business_error_string_is_a_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "tool.py").write_text("return 'Something went wrong'\n", encoding="utf-8")
    assert run(capsule).value == "Something went wrong"

    (capsule / "tool.py").write_text("print('before failure')\nraise ValueError('broken')\n", encoding="utf-8")
    with pytest.raises(CapsuleExecutionError) as raised:
        run(capsule)
    run_dir = raised.value.run_dir
    assert "before failure" in (run_dir / "run.log").read_text()
    assert "ValueError: broken" in (run_dir / "run.log").read_text()
    assert json.loads((run_dir / "run.json").read_text())["status"] == "failed"
    assert not (run_dir / "result.json").exists()


def test_unsupported_return_value_fails_instead_of_being_stringified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "tool.py").write_text("return object()\n", encoding="utf-8")
    with pytest.raises(CapsuleExecutionError) as raised:
        run(capsule)
    assert "unsupported type object" in (raised.value.run_dir / "run.log").read_text()
    assert json.loads((raised.value.run_dir / "run.json").read_text())["status"] == "failed"
    assert not (raised.value.run_dir / "result.json").exists()


def test_integer_return_object_keys_fail_without_string_coercion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "tool.py").write_text("return {1: 'one'}\n", encoding="utf-8")
    with pytest.raises(CapsuleExecutionError) as raised:
        run(capsule)
    log = (raised.value.run_dir / "run.log").read_text()
    assert "object keys must be strings" in log
    assert not (raised.value.run_dir / "result.json").exists()


def test_secret_is_not_written_to_provenance_and_effective_env_is_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    manifest = (capsule / "capsule.toml").read_text().replace(
        '[inputs]\nflag = true\n', '[env]\nTOKEN = "CAPSULE_SECRET"\n'
    )
    (capsule / "capsule.toml").write_text(manifest, encoding="utf-8")
    (capsule / "tool.py").write_text("return TOKEN == 'secret-value'\n", encoding="utf-8")
    monkeypatch.setenv("CAPSULE_SECRET", "secret-value")
    result = run(capsule)
    assert result.value is True
    assert "secret-value" not in (result.run_dir / "run.json").read_text()


def test_runtime_global_replaces_unset_env_reference_before_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "capsule.toml").write_text(
        'name = "sample"\ntool = "tool.py"\n[env]\nTOKEN = "UNSET_OVERRIDDEN_TOKEN"\n',
        encoding="utf-8",
    )
    (capsule / "tool.py").write_text("return TOKEN\n", encoding="utf-8")
    monkeypatch.delenv("UNSET_OVERRIDDEN_TOKEN", raising=False)
    assert run(capsule, globals={"TOKEN": "runtime wins"}).value == "runtime wins"


def test_duplicate_global_sources_in_one_manifest_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "capsule.toml").write_text(
        'name = "sample"\ntool = "tool.py"\n[globals]\nTOKEN = "literal"\n'
        '[env]\nTOKEN = "TOKEN_FROM_ENV"\n',
        encoding="utf-8",
    )
    (capsule / "tool.py").write_text("return TOKEN\n", encoding="utf-8")
    with pytest.raises(CapsuleConfigError, match=r"both \[globals\] and \[env\]"):
        run(capsule)


def test_input_and_global_destinations_cannot_collide_in_one_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "capsule.toml").write_text(
        'name = "sample"\ntool = "tool.py"\n[inputs]\nshared = "input"\n'
        '[globals]\nshared = "global"\n',
        encoding="utf-8",
    )
    (capsule / "tool.py").write_text("return shared\n", encoding="utf-8")
    with pytest.raises(CapsuleConfigError, match=r"both \[inputs\].*global source"):
        run(capsule)


def test_effective_runtime_input_and_global_destinations_cannot_collide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "tool.py").write_text("return shared\n", encoding="utf-8")
    with pytest.raises(CapsuleConfigError, match="both effective inputs and globals"):
        run(capsule, inputs={"shared": "input"}, globals={"shared": "global"})


def test_generated_function_global_name_is_reserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "tool.py").write_text("return 1\n", encoding="utf-8")
    with pytest.raises(CapsuleConfigError, match="__py_capsule_execute__.*reserved"):
        run(capsule, globals={"__py_capsule_execute__": "caller value"})
    (capsule / "capsule.toml").write_text(
        'name = "sample"\ntool = "tool.py"\n'
        '[globals]\n__py_capsule_execute__ = "configured value"\n',
        encoding="utf-8",
    )
    with pytest.raises(CapsuleConfigError, match="__py_capsule_execute__.*reserved"):
        run(capsule)


def test_runs_are_unique_and_same_named_capsules_share_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, first_capsule = make_project(tmp_path, name="first-project")
    _, second_capsule = make_project(tmp_path, name="second-project")
    (first_capsule / "tool.py").write_text("return 1\n", encoding="utf-8")
    (second_capsule / "tool.py").write_text("return 2\n", encoding="utf-8")
    first = run(first_capsule)
    first_again = run(first_capsule)
    second = run(second_capsule)
    expected_parent = tmp_path / "home" / ".py_capsule" / "sample" / "runs"
    assert first.run_dir != first_again.run_dir
    assert first.run_dir.parent == first_again.run_dir.parent == second.run_dir.parent == expected_parent
    assert first.value == 1 and second.value == 2
    assert json.loads((second.run_dir / "run.json").read_text())["capsule_dir"] == str(
        second_capsule.resolve()
    )


def test_enabled_logging_stays_on_caller_stderr_and_json_stays_clean_on_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "tool.py").write_text(
        "print('ordinary activity')\nSession.log_event('session activity')\nreturn {'ok': True}\n",
        encoding="utf-8",
    )
    result = run(capsule, log_level="info")
    result.print_json()
    captured = capsys.readouterr()
    assert captured.out == '{"ok":true}\n'
    assert "ordinary activity" in captured.err
    assert "session activity" in captured.err


def test_error_level_mirrors_failures_but_not_session_or_stream_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    actual_uv = shutil.which("uv")
    assert actual_uv is not None
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'FAKE_UV_PROGRESS' >&2\n"
        f"exec {shlex.quote(actual_uv)} \"$@\"\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    (capsule / "tool.py").write_text(
        "Session.log_event('routine session event')\n"
        "print('routine stderr output', file=__import__('sys').stderr)\n"
        "raise ValueError('recognized tool failure')\n",
        encoding="utf-8",
    )
    with pytest.raises(CapsuleExecutionError):
        run(capsule, log_level="error")
    displayed = capsys.readouterr().err
    assert "ValueError: recognized tool failure" in displayed
    assert "routine session event" not in displayed
    assert "routine stderr output" not in displayed
    assert "FAKE_UV_PROGRESS" not in displayed
    run_log = next((tmp_path / "home" / ".py_capsule" / "sample" / "runs").glob("*/run.log"))
    persisted = run_log.read_text()
    assert "routine session event" in persisted
    assert "routine stderr output" in persisted
    assert "FAKE_UV_PROGRESS" in persisted

    (capsule / "tool.py").write_text("return True\n", encoding="utf-8")
    run(capsule, log_level="debug")
    assert "FAKE_UV_PROGRESS" in capsys.readouterr().err


def test_explicit_project_selection_and_call_from_an_unrelated_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    project, capsule = make_project(tmp_path)
    (capsule / "capsule.toml").write_text(
        'name = "sample"\ntool = "tool.py"\nproject = "../.."\n', encoding="utf-8"
    )
    (capsule / "tool.py").write_text("import os\nreturn os.getcwd()\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    old = Path.cwd()
    os.chdir(elsewhere)
    try:
        result = run(capsule)
    finally:
        os.chdir(old)
    assert result.value == str(project)

    # Nearest-project discovery uses the capsule location too, not the caller cwd.
    (capsule / "capsule.toml").write_text(
        'name = "sample"\ntool = "tool.py"\n', encoding="utf-8"
    )
    os.chdir(elsewhere)
    try:
        discovered = run(capsule)
    finally:
        os.chdir(old)
    assert discovered.value == str(project)


def test_missing_effective_env_variable_fails_before_snippet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "capsule.toml").write_text(
        'name = "sample"\ntool = "tool.py"\n[env]\nTOKEN = "UNSET_CAPSULE_TOKEN"\n',
        encoding="utf-8",
    )
    (capsule / "tool.py").write_text("return 'should not run'\n", encoding="utf-8")
    monkeypatch.delenv("UNSET_CAPSULE_TOKEN", raising=False)
    with pytest.raises(CapsuleExecutionError, match="UNSET_CAPSULE_TOKEN") as raised:
        run(capsule)
    assert json.loads((raised.value.run_dir / "run.json").read_text())["status"] == "failed"


def test_project_capsule_and_runtime_values_merge_by_name_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    project, capsule = make_project(tmp_path)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture-project"\nversion = "0.0.1"\n'
        '[tool.py_capsule.inputs]\n'
        'flag = true\ncount = 3\nempty = "project"\nnothing = "project"\n'
        'payload = { project = true, retained = "lower" }\n'
        'project_only = "inherited"\n'
        '[tool.py_capsule.globals]\n'
        'FROM_PROJECT = "project literal"\n'
        'CHANGE_TO_ENV = "project literal"\n'
        '[tool.py_capsule.env]\n'
        'FROM_PROJECT_ENV = "PROJECT_CAPSULE_ENV_VALUE"\n'
        'CHANGE_TO_LITERAL = "UNSET_LOWER_PROJECT_ENV"\n'
        'RUNTIME_SOURCE = "UNSET_RUNTIME_SOURCE_ENV"\n',
        encoding="utf-8",
    )
    (capsule / "capsule.toml").write_text(
        'name = "sample"\ntool = "tool.py"\n'
        '[inputs]\ncount = 5\npayload = { capsule = true }\n'
        'capsule_only = "capsule value"\n'
        '[globals]\nCHANGE_TO_LITERAL = "capsule literal"\n'
        '[env]\nCHANGE_TO_ENV = "CAPSULE_ENV_VALUE"\n',
        encoding="utf-8",
    )
    (capsule / "tool.py").write_text(
        "return {"
        "'flag': flag, 'count': count, 'empty': empty, 'nothing': nothing, "
        "'payload': payload, 'project_only': project_only, "
        "'capsule_only': capsule_only, 'from_project': FROM_PROJECT, "
        "'from_project_env': FROM_PROJECT_ENV, 'change_to_env': CHANGE_TO_ENV, "
        "'change_to_literal': CHANGE_TO_LITERAL, 'runtime_source': RUNTIME_SOURCE"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROJECT_CAPSULE_ENV_VALUE", "project env value")
    monkeypatch.setenv("CAPSULE_ENV_VALUE", "capsule env value")
    monkeypatch.delenv("UNSET_LOWER_PROJECT_ENV", raising=False)
    monkeypatch.delenv("UNSET_RUNTIME_SOURCE_ENV", raising=False)

    result = run(
        capsule,
        inputs={
            "flag": False,
            "count": 0,
            "empty": "",
            "nothing": None,
            "payload": {"runtime": True},
        },
        globals={"FROM_PROJECT": "runtime literal", "RUNTIME_SOURCE": "runtime value"},
    )

    assert result.value == {
        "flag": False,
        "count": 0,
        "empty": "",
        "nothing": None,
        "payload": {"runtime": True},
        "project_only": "inherited",
        "capsule_only": "capsule value",
        "from_project": "runtime literal",
        "from_project_env": "project env value",
        "change_to_env": "capsule env value",
        "change_to_literal": "capsule literal",
        "runtime_source": "runtime value",
    }
    capsule_defaults = run(capsule, globals={"RUNTIME_SOURCE": "runtime value"})
    assert capsule_defaults.value["count"] == 5
    assert capsule_defaults.value["payload"] == {"capsule": True}


def test_project_layer_same_file_duplicates_fail_before_higher_layer_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    project, capsule = make_project(tmp_path)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture-project"\nversion = "0.0.1"\n'
        '[tool.py_capsule.inputs]\nshared = "project input"\n'
        '[tool.py_capsule.globals]\nshared = "project global"\n',
        encoding="utf-8",
    )
    (capsule / "capsule.toml").write_text(
        'name = "sample"\ntool = "tool.py"\n[inputs]\nshared = "capsule override"\n',
        encoding="utf-8",
    )
    (capsule / "tool.py").write_text("return shared\n", encoding="utf-8")
    with pytest.raises(CapsuleConfigError, match="both \\[inputs\\].*global source") as raised:
        run(capsule)
    assert str(project / "pyproject.toml") in str(raised.value)


def test_project_capsule_and_runtime_log_level_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    isolated_home(monkeypatch, tmp_path)
    project, capsule = make_project(tmp_path)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture-project"\nversion = "0.0.1"\n'
        '[tool.py_capsule]\nlog_level = "info"\n',
        encoding="utf-8",
    )
    (capsule / "tool.py").write_text(
        "Session.log_event('visible at info')\nreturn True\n", encoding="utf-8"
    )
    run(capsule)
    assert "visible at info" in capsys.readouterr().err

    manifest = (capsule / "capsule.toml").read_text()
    (capsule / "capsule.toml").write_text(
        manifest.replace("[inputs]", 'log_level = "error"\n[inputs]'), encoding="utf-8"
    )
    run(capsule)
    assert capsys.readouterr().err == ""
    run(capsule, log_level="info")
    assert "visible at info" in capsys.readouterr().err

    with pytest.raises(CapsuleConfigError, match="log_level") as raised:
        run(capsule, log_level="trace")
    assert raised.value.run_dir is not None
    assert "run evidence" in str(raised.value)
    metadata = json.loads((raised.value.run_dir / "run.json").read_text())
    assert metadata["status"] == "failed"
    assert metadata["name"] == "sample"
    assert "log_level" in (raised.value.run_dir / "run.log").read_text()


def test_project_config_rejects_invalid_log_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    project, capsule = make_project(tmp_path)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture-project"\nversion = "0.0.1"\n'
        '[tool.py_capsule]\nlog_level = "trace"\n',
        encoding="utf-8",
    )
    (capsule / "tool.py").write_text("return True\n", encoding="utf-8")
    with pytest.raises(CapsuleConfigError, match="log_level") as raised:
        run(capsule)
    assert raised.value.run_dir is not None
    metadata = json.loads((raised.value.run_dir / "run.json").read_text())
    assert metadata["name"] == "sample"
    assert metadata["status"] == "failed"
    assert metadata["error_type"] == "CapsuleConfigError"
    assert "log_level" in (raised.value.run_dir / "run.log").read_text()


def test_capsule_config_rejects_invalid_log_level_with_run_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "capsule.toml").write_text(
        'name = "sample"\ntool = "tool.py"\nlog_level = "trace"\n',
        encoding="utf-8",
    )
    (capsule / "tool.py").write_text("return True\n", encoding="utf-8")

    with pytest.raises(CapsuleConfigError, match="log_level") as raised:
        run(capsule)

    assert raised.value.run_dir is not None
    metadata = json.loads((raised.value.run_dir / "run.json").read_text())
    assert metadata["name"] == "sample"
    assert metadata["status"] == "failed"
    assert "log_level" in (raised.value.run_dir / "run.log").read_text()


def test_invalid_runtime_mapping_retains_failed_attempt_after_capsule_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "tool.py").write_text("return True\n", encoding="utf-8")

    with pytest.raises(CapsuleConfigError, match="inputs.*identifier") as raised:
        run(capsule, inputs={"not-valid": "value"})

    assert raised.value.run_dir is not None
    metadata = json.loads((raised.value.run_dir / "run.json").read_text())
    assert metadata["status"] == "failed"
    assert metadata["capsule_dir"] == str(capsule.resolve())
    assert "not-valid" in (raised.value.run_dir / "run.log").read_text()


def test_manifest_without_usable_name_does_not_create_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home(monkeypatch, tmp_path)
    _, capsule = make_project(tmp_path)
    (capsule / "capsule.toml").write_text(
        'name = "../escape"\ntool = "tool.py"\n', encoding="utf-8"
    )
    with pytest.raises(CapsuleConfigError, match="single path component") as raised:
        run(capsule)

    assert raised.value.run_dir is None
    assert not (tmp_path / "home/.py_capsule").exists()


def test_documented_wrapper_executes_example_shape_against_local_http_fixture(
    tmp_path: Path,
) -> None:
    received: list[dict[str, object]] = []

    class FixtureHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": body,
                }
            )
            if body.get("record_id") == "error-case":
                reply = {"success": False, "error": {"message": "fixture business error"}}
            elif body.get("record_id") == "exception-case":
                reply = {"success": True}
            else:
                reply = {
                    "success": True,
                    "data": {"record_id": body["record_id"], "source": "fixture"},
                }
            encoded = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(tmp_path / "home"),
                "PYCAPSULE_EXAMPLE_TEST_API_KEY": "fixture-test-key",
                "PYCAPSULE_EXAMPLE_LIVE_API_KEY": "fixture-live-key",
                "PY_CAPSULE_FIXTURE_URL": f"http://127.0.0.1:{server.server_port}",
                "RECORD_ID": "runtime-record",
            }
        )
        missing_secret_env = dict(env)
        del missing_secret_env["PYCAPSULE_EXAMPLE_TEST_API_KEY"]
        missing_secret = subprocess.run(
            [str(REPOSITORY / "examples/bin/fetch-record")],
            cwd=tmp_path,
            env=missing_secret_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=120,
        )
        assert missing_secret.returncode != 0
        assert "PYCAPSULE_EXAMPLE_TEST_API_KEY" in missing_secret.stderr
        assert received == []

        completed = subprocess.run(
            [str(REPOSITORY / "examples/bin/fetch-record")],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout) == {
            "record_id": "runtime-record",
            "source": "fixture",
        }
        assert received == [
            {
                "path": "/v1/hooks/inbound",
                "authorization": "Bearer fixture-test-key",
                "body": {"event": "FETCH_RECORD", "record_id": "runtime-record"},
            }
        ]

        env["RECORD_ID"] = "error-case"
        env["PYCAPSULE_LOG_LEVEL"] = "info"
        failed_business_call = subprocess.run(
            [str(REPOSITORY / "examples/bin/fetch-record")],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=120,
        )
        assert failed_business_call.returncode == 0, failed_business_call.stderr
        assert json.loads(failed_business_call.stdout) == "Something went wrong"
        assert failed_business_call.stdout == '"Something went wrong"\n'
        assert "fixture business error" in failed_business_call.stderr
        assert len(received) == 2

        env["RECORD_ID"] = "exception-case"
        execution_failure = subprocess.run(
            [str(REPOSITORY / "examples/bin/fetch-record")],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=120,
        )
        assert execution_failure.returncode != 0
        assert execution_failure.stdout == ""
        assert "run evidence" in execution_failure.stderr
        assert len(received) == 3

        runs = list((tmp_path / "home/.py_capsule/fetch_record/runs").iterdir())
        assert len(runs) == 4
        error_run_log = next(
            run_dir / "run.log"
            for run_dir in runs
            if "fixture business error" in (run_dir / "run.log").read_text()
        )
        persisted = error_run_log.read_text()
        assert "fixture business error" in persisted
        assert any(
            (run_dir / "result.json").is_file()
            and json.loads((run_dir / "result.json").read_text()) == "Something went wrong"
            for run_dir in runs
        )
        execution_failure_record = next(
            run_dir / "run.json"
            for run_dir in runs
            if json.loads((run_dir / "run.json").read_text())["status"] == "failed"
            and "KeyError" in (run_dir / "run.log").read_text()
        )
        assert json.loads(execution_failure_record.read_text())["status"] == "failed"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
