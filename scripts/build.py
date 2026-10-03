"""Validate fresh artifacts locally; only replace dist after checks succeed."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    os.chdir(ROOT)
    with tempfile.TemporaryDirectory(prefix="py-capsule-build-") as directory:
        temporary = Path(directory)
        artifacts = temporary / "dist"
        subprocess.run(
            ["uv", "build", "--no-sources", "--out-dir", str(artifacts)], check=True
        )
        (wheel,) = artifacts.glob("*.whl")
        (sdist,) = artifacts.glob("*.tar.gz")
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
            [str(python), str(ROOT / "examples/run_canonical_examples.py")],
            cwd=temporary,
            env=env,
            check=True,
        )
        destination = ROOT / "dist"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(artifacts, destination)
        print(f"Validated wheel and sdist: {destination}")


if __name__ == "__main__":
    main()
