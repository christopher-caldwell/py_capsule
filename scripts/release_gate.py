"""License and tag prerequisites shared by local releases and GitHub Actions."""

from pathlib import Path
import subprocess
import sys
import tomllib


def check_release_gate(tag: str) -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    if tag != f"v{project['version']}":
        raise RuntimeError("release tag must exactly match project.version")
    if not isinstance(project.get("license"), str) or not project["license"].strip():
        raise RuntimeError("choose an SPDX license and declare [project].license")
    patterns = project.get("license-files")
    if not isinstance(patterns, list) or not patterns:
        raise RuntimeError("declare [project].license-files before publishing")
    tracked = set(
        subprocess.check_output(["git", "ls-files", "-z"], text=True).split("\0")
    )
    declared: set[str] = set()
    for pattern in patterns:
        matches = [p for p in Path(".").glob(pattern) if p.is_file()]
        if not matches or any(p.as_posix() not in tracked for p in matches):
            raise RuntimeError(
                f"license pattern {pattern!r} must match committed files"
            )
        declared.update(p.as_posix() for p in matches)
    root_licenses = {
        p.as_posix()
        for pattern in ("LICENSE*", "COPYING*")
        for p in Path(".").glob(pattern)
        if p.is_file() and p.stat().st_size
    }
    if not root_licenses.intersection(declared):
        raise RuntimeError(
            "commit a nonempty root LICENSE* or COPYING* file and declare it"
        )


if __name__ == "__main__":
    try:
        check_release_gate(sys.argv[1])
    except RuntimeError as exc:
        sys.exit(f"public release blocked: {exc}")
