"""Reject damaged distribution candidates before they can be published."""

import importlib.metadata
import io
from pathlib import Path
import tarfile
import zipfile

import pytest
import py_capsule

from scripts.validate_artifacts import (
    check_installed,
    compare_wheels,
    inspect_distributions,
)

ROOT = Path(__file__).resolve().parents[1]


def distributions(directory: Path) -> tuple[Path, Path]:
    wheel = directory / "capsule_runner-0.0.1-py3-none-any.whl"
    sdist = directory / "capsule_runner-0.0.1.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        for source in (ROOT / "src/py_capsule").glob("*.py"):
            archive.writestr(f"py_capsule/{source.name}", source.read_bytes())
        archive.writestr(
            "capsule_runner-0.0.1.dist-info/METADATA", "Name: capsule-runner"
        )
    with tarfile.open(sdist, "w:gz") as archive:
        for source in [
            ROOT / "pyproject.toml",
            ROOT / "README.md",
            *(ROOT / "src/py_capsule").glob("*.py"),
        ]:
            archive.add(
                source, arcname=f"capsule_runner-0.0.1/{source.relative_to(ROOT)}"
            )
    return wheel, sdist


def test_distribution_inspection_accepts_library_artifacts(tmp_path: Path) -> None:
    expected = distributions(tmp_path)
    assert inspect_distributions(tmp_path) == expected


@pytest.mark.parametrize("entry", ["py_capsule/accidental.py", "scripts/release.py"])
def test_distribution_inspection_rejects_extra_wheel_code(
    tmp_path: Path, entry: str
) -> None:
    wheel, _ = distributions(tmp_path)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr(entry, "print('unexpected')")
    with pytest.raises(SystemExit, match="wheel"):
        inspect_distributions(tmp_path)


@pytest.mark.parametrize("damage", ["missing-source", "generated-path"])
def test_distribution_inspection_rejects_bad_sdist(tmp_path: Path, damage: str) -> None:
    _, sdist = distributions(tmp_path)
    with tarfile.open(sdist, "r:gz") as archive:
        contents = {}
        for item in archive.getmembers():
            if item.isfile():
                stream = archive.extractfile(item)
                assert stream is not None
                contents[item.name] = stream.read()
    if damage == "missing-source":
        del contents["capsule_runner-0.0.1/src/py_capsule/api.py"]
    else:
        contents["capsule_runner-0.0.1/.venv/generated"] = b"accidental"
    with tarfile.open(sdist, "w:gz") as archive:
        for name, data in contents.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
    with pytest.raises(SystemExit, match="sdist"):
        inspect_distributions(tmp_path)


def test_distribution_inspection_requires_exactly_one_pair(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="expected one wheel and one sdist"):
        inspect_distributions(tmp_path)
    wheel, _ = distributions(tmp_path)
    (tmp_path / "duplicate.whl").write_bytes(wheel.read_bytes())
    with pytest.raises(SystemExit, match="expected one wheel and one sdist"):
        inspect_distributions(tmp_path)


def test_rebuilt_wheel_compares_sources_not_metadata(tmp_path: Path) -> None:
    wheel, _ = distributions(tmp_path)
    rebuilt = tmp_path / "rebuilt.whl"
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(rebuilt, "w") as target:
        for name in source.namelist():
            target.writestr(
                name, source.read(name) if name.endswith(".py") else b"new metadata"
            )
    compare_wheels(wheel, rebuilt)
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(rebuilt, "w") as target:
        for name in source.namelist():
            target.writestr(
                name,
                b"changed source" if name == "py_capsule/api.py" else source.read(name),
            )
    with pytest.raises(SystemExit, match="same library sources"):
        compare_wheels(wheel, rebuilt)


def test_installed_check_rejects_checkout_import() -> None:
    with pytest.raises(SystemExit, match="checkout instead of wheel"):
        check_installed(ROOT)


def test_installed_check_rejects_wrong_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        py_capsule, "__file__", str(tmp_path / "py_capsule/__init__.py")
    )
    monkeypatch.setattr(importlib.metadata, "version", lambda _: "9.9.9")
    with pytest.raises(AssertionError):
        check_installed(ROOT)
