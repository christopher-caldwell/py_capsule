"""Artifact checks shared by local builds and release CI.

The installed check runs with the wheel's interpreter from outside the checkout.
All commands use only the standard library and the installed package.
"""

import argparse
import hashlib
from pathlib import Path
import tarfile
import tomllib
import zipfile


def inspect_distributions(dist: Path) -> tuple[Path, Path]:
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(
            f"expected one wheel and one sdist; found {len(wheels)} wheel(s) "
            f"and {len(sdists)} sdist(s)"
        )

    expected_package_files = {
        "py_capsule/__init__.py",
        "py_capsule/_config.py",
        "py_capsule/_worker.py",
        "py_capsule/api.py",
    }
    forbidden_markers = (
        "/.git/",
        "/.venv/",
        "/__pycache__/",
        "/.pytest_cache/",
        "/build/",
        "/dist/",
    )

    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
        package_files = {
            name
            for name in wheel_names
            if name.startswith("py_capsule/") and not name.endswith("/")
        }
        if package_files != expected_package_files:
            raise SystemExit(
                "wheel package contents differ from the supported library surface:\n"
                + "\n".join(sorted(package_files))
            )
        unexpected_roots = {
            name.split("/", 1)[0]
            for name in wheel_names
            if not (
                name.startswith("py_capsule/")
                or (name.startswith("capsule_runner-") and ".dist-info/" in name)
            )
        }
        if unexpected_roots:
            raise SystemExit(
                "unexpected wheel top-level entries: "
                + ", ".join(sorted(unexpected_roots))
            )

    sdist = sdists[0]
    with tarfile.open(sdist, "r:gz") as archive:
        raw_names = archive.getnames()
        stripped = {name.split("/", 1)[1] for name in raw_names if "/" in name}
        required = {
            "pyproject.toml",
            "README.md",
            "src/py_capsule/__init__.py",
            "src/py_capsule/_config.py",
            "src/py_capsule/_worker.py",
            "src/py_capsule/api.py",
        }
        missing = required - stripped
        if missing:
            raise SystemExit(
                "sdist is missing required files: " + ", ".join(sorted(missing))
            )
        for name in raw_names:
            normalized = "/" + name.strip("/") + "/"
            if any(marker in normalized for marker in forbidden_markers):
                raise SystemExit(f"forbidden generated path in sdist: {name}")

    for artifact in (wheel, sdist):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        print(f"{digest}  {artifact}")
    return wheel, sdist


def check_installed(checkout: Path) -> None:
    from importlib.metadata import version
    import py_capsule
    from py_capsule import (
        CapsuleConfigError,
        CapsuleExecutionError,
        CapsuleResult,
        run,
    )

    installed = Path(py_capsule.__file__).resolve()
    checkout = checkout.resolve()
    if installed.is_relative_to(checkout):
        raise SystemExit(
            f"py_capsule imported from checkout instead of wheel: {installed}"
        )

    project = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert version(project["name"]) == project["version"]
    assert all(
        symbol is not None
        for symbol in (CapsuleConfigError, CapsuleExecutionError, CapsuleResult, run)
    )
    print(f"installed py_capsule from {installed}")


def compare_wheels(original: Path, rebuilt: Path) -> None:
    with zipfile.ZipFile(original) as first, zipfile.ZipFile(rebuilt) as second:
        first_sources = {
            name: first.read(name)
            for name in first.namelist()
            if name.startswith("py_capsule/") and name.endswith(".py")
        }
        second_sources = {
            name: second.read(name)
            for name in second.namelist()
            if name.startswith("py_capsule/") and name.endswith(".py")
        }

    if first_sources != second_sources:
        raise SystemExit(
            "wheel rebuilt from sdist does not contain the same library sources"
        )
    print("sdist rebuilt to an equivalent library wheel")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect").add_argument("dist", type=Path)
    commands.add_parser("installed").add_argument("checkout", type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("original", type=Path)
    compare.add_argument("rebuilt", type=Path)
    args = parser.parse_args()
    if args.command == "inspect":
        inspect_distributions(args.dist)
    elif args.command == "installed":
        check_installed(args.checkout)
    else:
        compare_wheels(args.original, args.rebuilt)


if __name__ == "__main__":
    main()
