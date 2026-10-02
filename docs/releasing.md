# Releasing PyCapsule

PyCapsule is prepared to publish through PyPI Trusted Publishing. The release
workflow builds and validates the distributions before the publishing job and
passes the exact same artifacts to PyPI. No long-lived PyPI token belongs in
GitHub secrets.

## Current first-release candidate

The first public release candidate is `py-capsule 0.1.0`.

Public publication is intentionally blocked until both of these owner decisions
are resolved:

1. Choose the project license, commit the license text, and declare both the
   SPDX license expression and `license-files` in `[project]` metadata. Do
   not publish until this is explicit.
2. Confirm that public PyPI publication is intended. This GitHub repository is
   private, but wheel and source-distribution contents uploaded to public PyPI
   are public regardless of repository visibility.

Project/repository URLs should only be added to package metadata if the
destinations are meant to be reachable by package users.

## One-time PyPI and GitHub setup

Create a GitHub Environment named `pypi` and require manual approval for
deployments to it.

For a brand-new PyPI project, add a pending Trusted Publisher from the PyPI
account publishing settings with these values:

- PyPI project name: `py-capsule`
- GitHub owner: `christopher-caldwell`
- GitHub repository: `py_capsule`
- Workflow filename: `release.yml`
- Environment: `pypi`

A pending publisher does not reserve the PyPI name. Verify the name again
immediately before the first release. The first successful trusted publication
creates the PyPI project.

No PyPI API token is needed.

## Release procedure

1. Update `project.version` in `pyproject.toml`.
2. Let the package workflow pass on the pull request. It tests the supported
   Python versions, canonical examples, wheel/sdist contents, a clean wheel
   installation, and an sdist rebuild.
3. Merge the release-ready revision to `main`.
4. Create a GitHub Release whose tag is exactly `v<project.version>` (for the
   first release, `v0.1.0`) at the intended release commit.
5. Publish the GitHub Release.
6. Review the queued `pypi` environment deployment and approve it only after
   confirming the tag, commit, version, artifacts, and public-release intent.
7. After PyPI accepts the release, install the published version from a separate
   project and update downstream consumers from Git source wiring to a normal
   versioned dependency.

The workflow refuses a release tag that does not match `project.version`, and
it refuses public publication while the SPDX license expression,
`license-files` metadata, or a root license file is missing.

## What the workflow validates

The workflow runs the repository tests on Python 3.11 through 3.14, runs the
canonical examples, builds one wheel and one source distribution, runs strict
metadata checks, inspects the archives, records SHA-256 hashes, installs the
wheel into a clean virtual environment, verifies the supported public imports,
executes the canonical capsules through the installed wheel, rebuilds a wheel
from the sdist, and compares the packaged library sources.

The publishing job does not rebuild. It downloads the distributions produced by
the validated build job and publishes those exact files with OIDC.

TestPyPI is intentionally not part of the default path. The isolated wheel test
is the primary package acceptance check; a second publication pipeline would
add ceremony without proving more about the installed artifact. TestPyPI can be
used manually later if a release-specific reason makes it useful.

## Downstream migration after publication

A consumer can replace a Git source override with a normal dependency such as:

```toml
[project]
dependencies = [
    "py-capsule==0.1.0",
]
```

Then regenerate the consumer lock file. No capsule body, runtime adapter, or
stage file should need to change merely because the dependency source changes.
