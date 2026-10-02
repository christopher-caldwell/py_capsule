"""Small owner-driven release command. Artifact upload belongs to GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts.release_gate import check_release_gate

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "christopher-caldwell/py_capsule"
RELEASE_FILES = ["pyproject.toml", "uv.lock", "examples/wrapper/uv.lock"]


def run(*args: str) -> str:
    if args[0] == "just":
        subprocess.run(args, check=True)
        return ""
    try:
        return subprocess.check_output(args, text=True).strip()
    except subprocess.CalledProcessError as exc:
        if exc.output:
            print(exc.output, file=sys.stderr)
        raise


def git(*args: str) -> str:
    return run("git", *args)


def api(endpoint: str) -> Any:
    return json.loads(run("gh", "api", f"repos/{REPOSITORY}/{endpoint}"))


def clean() -> None:
    if git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("working tree must be clean, including untracked files")


def releases() -> dict[str, bool]:
    # Paginate so a missing result really means absent, including private drafts.
    pages = json.loads(
        run("gh", "api", "--paginate", "--slurp", f"repos/{REPOSITORY}/releases")
    )
    return {item["tag_name"]: item["draft"] for page in pages for item in page}


def check_index(version: str) -> None:
    try:
        with urlopen("https://pypi.org/pypi/py-capsule/json", timeout=20) as response:
            data = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return
        raise
    if version in data["releases"]:
        raise RuntimeError(f"PyPI already knows py-capsule {version}; refusing reuse")
    print("PyPI project exists; its owner must have configured Trusted Publishing.")


def tag_commit(tag: str) -> str | None:
    if tag not in git("tag", "--list").splitlines():
        return None
    return git("rev-parse", f"refs/tags/{tag}^{{commit}}")


def preflight() -> tuple[str, str, dict[str, bool]]:
    for tool in ("git", "uv", "gh", "just"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"required tool is missing: {tool}")
    if git("branch", "--show-current") != "main":
        raise RuntimeError("releases must run on main; merge the preparation PR first")
    clean()
    # Guard both fetch and push destinations, including multiple push URLs.
    allowed = {
        f"git@github.com:{REPOSITORY}.git",
        f"https://github.com/{REPOSITORY}.git",
        f"https://github.com/{REPOSITORY}",
        f"ssh://git@github.com/{REPOSITORY}.git",
    }
    for options in (("--all",), ("--push", "--all")):
        urls = git("remote", "get-url", *options, "origin").splitlines()
        if len(urls) != 1 or urls[0] not in allowed:
            raise RuntimeError(f"origin must point only to {REPOSITORY}")
    version = run("uv", "version", "--short")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("expected a major.minor.patch project.version")
    check_release_gate(f"v{version}")
    run("gh", "auth", "status", "--hostname", "github.com")
    repository = json.loads(run("gh", "api", f"repos/{REPOSITORY}"))
    if not repository.get("permissions", {}).get("push"):
        raise RuntimeError("GitHub authentication needs repository write access")
    api("environments/pypi")
    if api("actions/workflows/release.yml")["state"] != "active":
        raise RuntimeError("release.yml must be enabled on GitHub")
    git("fetch", "origin", "refs/heads/main:refs/remotes/origin/main", "--tags")
    head = git("rev-parse", "HEAD")
    if head != git("rev-parse", "origin/main"):
        raise RuntimeError(
            "main must equal origin/main; use git pull --ff-only if behind. "
            "For an interrupted local release, see docs/releasing.md."
        )
    return version, head, releases()


def create_release(tag: str) -> None:
    try:
        run(
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            REPOSITORY,
            "--verify-tag",
            "--generate-notes",
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"{tag} is safely pushed, but GitHub Release creation did not complete. "
            "Run just publish-resume; do not bump again."
        ) from exc
    print(f"Published GitHub Release {tag}; Actions now validates and uploads to PyPI.")


def publish(mode: str, bump: str | None = None) -> None:
    version, head, published = preflight()
    current_tag = f"v{version}"
    current_commit = tag_commit(current_tag)
    if mode == "resume":
        if current_commit != head:
            raise RuntimeError("resume requires the current version tag at HEAD")
        if git("cat-file", "-t", f"refs/tags/{current_tag}") != "tag":
            raise RuntimeError("resume requires an annotated tag")
        remote = git("ls-remote", "origin", f"refs/tags/{current_tag}")
        if not remote or remote.split()[0] != git(
            "rev-parse", f"refs/tags/{current_tag}"
        ):
            raise RuntimeError("resume requires the identical annotated tag on origin")
        if current_tag in published:
            if published[current_tag]:
                raise RuntimeError(
                    "a draft already exists; inspect and publish it on GitHub"
                )
            print(
                f"GitHub Release {current_tag} already exists; inspect its Actions run."
            )
            return
        check_index(version)
        run("just", "check", "build")
        clean()
        create_release(current_tag)
        return

    if current_commit is not None and current_tag not in published:
        raise RuntimeError(
            "current version has an unfinished release; use just publish-resume"
        )
    if current_tag in published and published[current_tag]:
        raise RuntimeError(
            "current version has a draft release; finish it before bumping"
        )
    if current_commit == head:
        raise RuntimeError("no new commits since the current release")
    if git("log", "-1", "--format=%s") == f"chore: release {current_tag}":
        raise RuntimeError(
            "release commit already prepared; see recovery in docs/releasing.md"
        )

    if mode == "initial":
        if version != "0.0.1" or published or git("tag", "--list", "v*"):
            raise RuntimeError(
                "publish-initial requires 0.0.1 and no prior releases/version tags"
            )
        candidate = version
    else:
        if current_tag not in published or current_commit is None:
            raise RuntimeError(
                "publish the current version first (use just publish-initial for 0.0.1)"
            )
        # uv computes the candidate without touching the working tree or locks.
        candidate = run("uv", "version", "--bump", str(bump), "--dry-run", "--short")
    tag = f"v{candidate}"
    if tag_commit(tag) is not None or tag in published:
        raise RuntimeError(f"tag or GitHub Release {tag} already exists")
    check_index(candidate)
    print(
        f"Preparing {tag}. Validation failures leave changes for inspection.",
        flush=True,
    )
    if mode == "bump":
        run("uv", "version", "--bump", str(bump), "--no-sync")
        run("uv", "lock", "--project", "examples/wrapper")
    check_release_gate(tag)
    run("just", "check", "build")
    if git("rev-parse", "HEAD") != head or git("branch", "--show-current") != "main":
        raise RuntimeError("branch or HEAD changed during validation")
    changed = set(git("diff", "--name-only", "HEAD").splitlines())
    untracked = git("ls-files", "--others", "--exclude-standard")
    if changed - set(RELEASE_FILES) or untracked:
        raise RuntimeError(
            "validation changed unexpected files; inspect before releasing"
        )
    # A concurrent main update or release must be noticed before local commit/tag.
    git("fetch", "origin", "refs/heads/main:refs/remotes/origin/main", "--tags")
    if git("rev-parse", "origin/main") != head:
        raise RuntimeError("origin/main moved during validation; inspect and retry")
    if tag_commit(tag) is not None or tag in releases():
        raise RuntimeError(f"{tag} appeared during validation; stopping")
    check_index(candidate)
    if mode == "bump":
        git("add", "--", *RELEASE_FILES)
        git("commit", "-m", f"chore: release {tag}")
    # The first version is already committed by its preparation PR.
    clean()
    git("tag", "-a", tag, "-m", f"Release {tag}")
    try:
        git(
            "push", "--atomic", "--no-follow-tags", "origin", "main", f"refs/tags/{tag}"
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"atomic push failed; local {tag} is retained. Follow docs/releasing.md "
            "to push this exact commit/tag, then run just publish-resume."
        ) from exc
    create_release(tag)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    commands.add_parser("bump").add_argument(
        "bump", choices=("patch", "minor", "major")
    )
    commands.add_parser("initial")
    commands.add_parser("resume")
    args = parser.parse_args()
    os.chdir(ROOT)
    try:
        publish(args.mode, getattr(args, "bump", None))
    except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
        sys.exit(f"release stopped: {exc}")


if __name__ == "__main__":
    main()
