"""Validate fresh artifacts locally; only replace dist after checks succeed."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile

from scripts.validate_artifacts import compare_wheels, inspect_distributions


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    os.chdir(ROOT)
    with tempfile.TemporaryDirectory(prefix="py-capsule-build-") as directory:
        temporary = Path(directory)
        artifacts = temporary / "dist"
        subprocess.run(
            ["uv", "build", "--no-sources", "--out-dir", str(artifacts)], check=True
        )
        wheel, sdist = inspect_distributions(artifacts)
        subprocess.run(
            [
                "uv",
                "run",
                "--locked",
                "twine",
                "check",
                "--strict",
                str(wheel),
                str(sdist),
            ],
            check=True,
        )
        environment = temporary / "venv"
        subprocess.run(["uv", "venv", str(environment)], check=True)
        python = environment / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
            check=True,
        )
        env = {**os.environ, "PYTHONPATH": "", "HOME": str(temporary / "home")}
        subprocess.run(
            [
                str(python),
                str(ROOT / "scripts/validate_artifacts.py"),
                "installed",
                str(ROOT),
            ],
            cwd=temporary,
            env=env,
            check=True,
        )
        subprocess.run(
            [str(python), str(ROOT / "examples/run_canonical_examples.py")],
            cwd=temporary,
            env=env,
            check=True,
        )
        rebuilt = temporary / "rebuilt"
        subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--no-sources",
                "--out-dir",
                str(rebuilt),
                str(sdist),
            ],
            check=True,
        )
        (rebuilt_wheel,) = rebuilt.glob("*.whl")
        compare_wheels(wheel, rebuilt_wheel)
        destination = ROOT / "dist"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(artifacts, destination)
        print(f"Validated wheel and sdist: {destination}")


if __name__ == "__main__":
    main()
