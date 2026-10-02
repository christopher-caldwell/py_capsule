# Show the everyday commands.
default:
    @just --list

# Format Python (preserving the supplied capsule bodies).
format:
    uv run --locked ruff format .

# Check Python formatting without changing files.
format-check:
    uv run --locked ruff format --check .

# Check Python for common errors with Ruff.
lint:
    uv run --locked ruff check .

# Type-check the library, tests, scripts, and example support code.
typecheck:
    uv run --locked ty check

# Run the test suite.
test:
    uv run --locked python -m pytest

# Check example locks and exercise all four canonical capsules locally.
examples:
    uv lock --check --project examples/wrapper
    uv lock --check --project examples/fixture-project
    uv run --frozen --project examples/wrapper python examples/run_canonical_examples.py

# Build wheel/sdist, check metadata, and exercise the installed wheel.
build:
    uv run --locked python -m scripts.build

# Everyday non-mutating validation.
check: format-check lint typecheck test examples

# Bump patch/minor/major, validate, commit, tag, push, and publish a GitHub Release.
publish bump:
    uv run --frozen --no-sync python -m scripts.release bump {{quote(bump)}}

# Publish the first version, 0.0.1, without bumping it.
publish-initial:
    uv run --frozen --no-sync python -m scripts.release initial

# Complete a prepared release at the current version, without another bump.
publish-resume:
    uv run --frozen --no-sync python -m scripts.release resume
