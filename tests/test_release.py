"""Release orchestration tests: all external commands and HTTP are substituted."""

import io
from email.message import Message
import json
from pathlib import Path
import subprocess
from urllib.error import HTTPError

import pytest

from scripts import release, release_gate


class Shell:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.version = "0.0.1"
        self.tags = {"v0.0.1": "previous"}
        self.published = {"v0.0.1": False}
        self.branch = "main"
        self.head = "head"
        self.remote = "head"
        self.dirty = ""
        self.changed = "pyproject.toml\nuv.lock\nexamples/wrapper/uv.lock"
        self.fail: tuple[str, ...] | None = None

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if self.fail and args[: len(self.fail)] == self.fail:
            raise subprocess.CalledProcessError(1, args)
        if args == ("git", "branch", "--show-current"):
            return self.branch
        if args[:2] == ("git", "status"):
            return self.dirty
        if args[:3] == ("git", "remote", "get-url"):
            return f"git@github.com:{release.REPOSITORY}.git"
        if args == ("uv", "version", "--short"):
            return self.version
        if args[:3] == ("uv", "version", "--bump"):
            candidate = {"patch": "0.0.2", "minor": "0.1.0", "major": "1.0.0"}[args[3]]
            if "--dry-run" not in args:
                self.version = candidate
            return candidate
        if args[:3] == ("gh", "auth", "status"):
            return ""
        if args[:2] == ("gh", "api"):
            if args[-1].endswith("/releases"):
                return json.dumps(
                    [[{"tag_name": k, "draft": v} for k, v in self.published.items()]]
                )
            if args[-1].endswith("/environments/pypi"):
                return "{}"
            if args[-1].endswith("/actions/workflows/release.yml"):
                return '{"state": "active"}'
            return '{"permissions": {"push": true}}'
        if args[:2] == ("git", "rev-parse"):
            if args[2] == "HEAD":
                return self.head
            if args[2] == "origin/main":
                return self.remote
            tag = args[2].removeprefix("refs/tags/").removesuffix("^{commit}")
            return self.tags[tag] if args[2].endswith("^{commit}") else "tag-object"
        if args[:3] == ("git", "tag", "--list"):
            return "\n".join(self.tags)
        if args[:3] == ("git", "tag", "-a"):
            self.tags[args[3]] = self.head
            return ""
        if args[:2] == ("git", "cat-file"):
            return "tag"
        if args[:2] == ("git", "ls-remote"):
            return f"tag-object\t{args[-1]}"
        if args[:2] == ("git", "log"):
            return "feat: meaningful work"
        if args[:2] == ("git", "diff"):
            return self.changed
        if args[:2] == ("git", "ls-files"):
            return ""
        if args[:2] == ("git", "commit"):
            self.head = "release-commit"
            return ""
        if args[:2] == ("git", "push"):
            self.remote = self.head
            return ""
        if args[:3] == ("gh", "release", "create"):
            self.published[args[3]] = False
            return ""
        if (
            args[:2] in (("git", "fetch"), ("git", "add"), ("uv", "lock"))
            or args[0] == "just"
        ):
            return ""
        raise AssertionError(f"Unexpected command: {args}")


@pytest.fixture
def shell(monkeypatch: pytest.MonkeyPatch) -> Shell:
    fake = Shell()
    monkeypatch.setattr(release, "run", fake)
    monkeypatch.setattr(release.shutil, "which", lambda _: "/test/tool")
    monkeypatch.setattr(release, "check_release_gate", lambda _: None)
    monkeypatch.setattr(
        release, "urlopen", lambda *a, **k: io.BytesIO(b'{"releases": {}}')
    )
    return fake


def assert_no_release(shell: Shell) -> None:
    assert not any(
        c[:2] in (("git", "commit"), ("git", "push"))
        or c[:3] in (("git", "tag", "-a"), ("gh", "release", "create"))
        for c in shell.calls
    )


@pytest.mark.parametrize(
    "bump,version", [("patch", "0.0.2"), ("minor", "0.1.0"), ("major", "1.0.0")]
)
def test_bump_validates_before_commit_and_pushes_atomically(
    shell: Shell, bump: str, version: str
) -> None:
    release.publish("bump", bump)
    calls = shell.calls
    assert shell.version == version
    assert calls.index(("uv", "lock", "--project", "examples/wrapper")) < calls.index(
        ("just", "check", "build")
    )
    assert calls.index(("just", "check", "build")) < calls.index(
        ("git", "commit", "-m", f"chore: release v{version}")
    )
    assert ("git", "tag", "-a", f"v{version}", "-m", f"Release v{version}") in calls
    push = (
        "git",
        "push",
        "--atomic",
        "--no-follow-tags",
        "origin",
        "main",
        f"refs/tags/v{version}",
    )
    assert calls.index(push) < next(
        i for i, c in enumerate(calls) if c[:3] == ("gh", "release", "create")
    )
    assert "--verify-tag" in calls[-1]
    assert not any("examples/fixture-project" in c for c in calls)


def test_initial_preserves_0001_without_empty_release_commit(shell: Shell) -> None:
    shell.tags = {}
    shell.published = {}
    shell.changed = ""
    release.publish("initial")
    assert shell.version == "0.0.1"
    assert not any(c[:2] == ("git", "commit") or "--bump" in c for c in shell.calls)
    assert "v0.0.1" in shell.published


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("branch", "feature", "main"),
        ("dirty", "?? local.txt", "clean"),
        ("remote", "other", "equal origin/main"),
        ("tags", {"v0.0.1": "previous", "v0.0.2": "conflict"}, "already exists"),
        ("published", {"v0.0.1": False, "v0.0.2": True}, "already exists"),
        ("published", {}, "unfinished"),
        ("published", {"v0.0.1": True}, "draft"),
        ("tags", {"v0.0.1": "head"}, "no new commits"),
    ],
)
def test_unsafe_state_fails_before_mutation(
    shell: Shell, field: str, value: object, message: str
) -> None:
    setattr(shell, field, value)
    with pytest.raises(RuntimeError, match=message):
        release.publish("bump", "patch")
    assert shell.version == "0.0.1"
    assert_no_release(shell)


@pytest.mark.parametrize("command", [("gh", "auth"), ("gh", "api"), ("git", "fetch")])
def test_network_or_auth_failure_is_not_treated_as_absence(
    shell: Shell, command: tuple[str, ...]
) -> None:
    shell.fail = command
    with pytest.raises(subprocess.CalledProcessError):
        release.publish("bump", "patch")
    assert shell.version == "0.0.1"
    assert_no_release(shell)


def test_validation_failure_keeps_bump_without_release(shell: Shell) -> None:
    shell.fail = ("just",)
    with pytest.raises(subprocess.CalledProcessError):
        release.publish("bump", "patch")
    assert shell.version == "0.0.2"
    assert_no_release(shell)


def test_unexpected_validation_changes_are_not_committed(shell: Shell) -> None:
    shell.changed += "\nsrc/py_capsule/api.py"
    with pytest.raises(RuntimeError, match="unexpected"):
        release.publish("bump", "patch")
    assert_no_release(shell)


def test_release_creation_failure_can_resume_without_bump(shell: Shell) -> None:
    shell.fail = ("gh", "release", "create")
    with pytest.raises(RuntimeError, match="publish-resume"):
        release.publish("bump", "patch")
    assert shell.version == "0.0.2"
    with pytest.raises(RuntimeError, match="unfinished"):
        release.publish("bump", "patch")
    shell.calls.clear()
    shell.fail = None
    release.publish("resume")
    assert not any("--bump" in c or c[:2] == ("git", "commit") for c in shell.calls)
    assert shell.published["v0.0.2"] is False
    # If gh timed out after successfully publishing, recovery is also harmless.
    shell.calls.clear()
    release.publish("resume")
    assert_no_release(shell)


def test_atomic_push_failure_does_not_create_github_release(shell: Shell) -> None:
    shell.fail = ("git", "push")
    with pytest.raises(RuntimeError, match="atomic push failed"):
        release.publish("bump", "patch")
    assert not any(c[:3] == ("gh", "release", "create") for c in shell.calls)
    with pytest.raises(RuntimeError, match="equal origin/main"):
        release.publish("bump", "patch")


def test_duplicate_pypi_version_blocks_before_bump(
    shell: Shell, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        release, "urlopen", lambda *a, **k: io.BytesIO(b'{"releases": {"0.0.2": []}}')
    )
    with pytest.raises(RuntimeError, match="PyPI already"):
        release.publish("bump", "patch")
    assert shell.version == "0.0.1"
    assert_no_release(shell)


@pytest.mark.parametrize("code", [404, 403, 500])
def test_pypi_http_errors_only_allow_404(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise HTTPError("https://pypi.org", code, "test", Message(), None)

    monkeypatch.setattr(release, "urlopen", fail)
    if code == 404:
        release.check_index("0.0.1")
    else:
        with pytest.raises(HTTPError):
            release.check_index("0.0.1")


def test_missing_license_blocks_before_network(
    shell: Shell, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="0.0.1"\n')
    monkeypatch.setattr(release, "check_release_gate", release_gate.check_release_gate)
    with pytest.raises(RuntimeError, match="license"):
        release.publish("bump", "patch")
    assert not any(c[0] == "gh" for c in shell.calls)
    assert_no_release(shell)


def test_license_gate_requires_committed_declared_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion="0.0.1"\nlicense="MIT"\nlicense-files=["LICENSE"]\n'
    )
    monkeypatch.setattr(
        release_gate.subprocess, "check_output", lambda *a, **k: "LICENSE\0"
    )
    with pytest.raises(RuntimeError, match="committed"):
        release_gate.check_release_gate("v0.0.1")
    (tmp_path / "LICENSE").write_text(
        "Test fixture only; no license chosen for PyCapsule."
    )
    release_gate.check_release_gate("v0.0.1")
    with pytest.raises(RuntimeError, match="match project.version"):
        release_gate.check_release_gate("v9.0.0")
    monkeypatch.setattr(release_gate.subprocess, "check_output", lambda *a, **k: "")
    with pytest.raises(RuntimeError, match="committed"):
        release_gate.check_release_gate("v0.0.1")


def test_missing_tool_blocks_early(
    shell: Shell, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="missing"):
        release.publish("bump", "patch")
    assert not shell.calls


def test_remote_advancing_during_validation_stops_commit(
    shell: Shell, monkeypatch: pytest.MonkeyPatch
) -> None:
    def execute(*args: str) -> str:
        result = shell(*args)
        if args[0] == "just":
            shell.remote = "concurrent-commit"
        return result

    monkeypatch.setattr(release, "run", execute)
    with pytest.raises(RuntimeError, match="moved during validation"):
        release.publish("bump", "patch")
    assert_no_release(shell)


@pytest.mark.parametrize("kind", ["lightweight", "missing-remote", "different-remote"])
def test_resume_rejects_unverified_tag(
    shell: Shell, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    shell.tags = {"v0.0.1": "head"}
    shell.published = {}

    def execute(*args: str) -> str:
        if args[:2] == ("git", "cat-file") and kind == "lightweight":
            return "commit"
        if args[:2] == ("git", "ls-remote") and kind != "lightweight":
            return (
                "" if kind == "missing-remote" else "different-object\trefs/tags/v0.0.1"
            )
        return shell(*args)

    monkeypatch.setattr(release, "run", execute)
    with pytest.raises(RuntimeError, match="annotated tag"):
        release.publish("resume")
    assert_no_release(shell)
