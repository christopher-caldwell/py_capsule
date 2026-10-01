from __future__ import annotations

import json
import os
import importlib.util
import shutil
import shlex
from pathlib import Path

import pytest

from py_capsule import CapsuleConfigError, CapsuleExecutionError, run


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
