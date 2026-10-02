# Releasing PyCapsule

The first public version is **0.0.1**, with Alpha development status. The API may
change in any `0.x` release, including patches; consumers should pin exact versions.
This is a normal installable Python version, not a promise of 1.0 API stability.

PyPI is the Python package index used by pip and uv. A published GitHub Release
triggers `.github/workflows/release.yml`, which validates and uploads the exact
built artifacts to PyPI using OIDC / Trusted Publishing. The local command never
uploads to PyPI or needs a PyPI token. Repository visibility does not make files
published to public PyPI private.

## One-time setup

- Install Python 3.11+, `uv`, `just`, Git, and the GitHub CLI (`gh`). The recipes
  manage Ruff, ty, pytest, and Twine through `uv.lock`. Use a current uv with
  `uv version --bump` support (tested locally with 0.10.9; CI pins 0.12.21).
- Run `gh auth login --hostname github.com` and configure Git push access.
  The authenticated account needs write access to this repository. Local Git
  author identity and any commit/tag signing configuration must also work.
- **Choose a license.** Commit its nonempty root `LICENSE*` or `COPYING*` file;
  declare an SPDX string in `[project].license` and matching paths/globs in
  `[project].license-files`. This task intentionally does not choose a license.
  The shared local/CI gate blocks publication until these are present.
- Create a GitHub environment named `pypi`. Configure required reviewers if your
  repository plan supports them; then each upload waits for an approval there.
  Ensure repository Actions and the release workflow are enabled, and that tag
  deployment restrictions permit `v*` tags. Branch/tag rules must permit the
  owner's release commit and annotated tag to be pushed; the command bypasses no
  protection and never force-pushes.
- For a new PyPI project, configure a **pending Trusted Publisher** in your PyPI
  account with project `py-capsule`, owner `christopher-caldwell`, repository
  `py_capsule`, workflow `release.yml`, and environment `pypi`. If the project
  already exists, confirm ownership and add its ordinary Trusted Publisher.
  A pending publisher does not reserve the name. No API token is required.

The command verifies the GitHub environment and active workflow, but cannot
inspect your private PyPI publisher configuration or guarantee branch-rule,
environment-approval, or name-ownership eligibility. Finish that setup first.
See [PyPI's pending publisher guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

## Normal workflow

```sh
just check
just build
# Merge the feature/fix PR, then:
git switch main
git pull --ff-only
just publish patch      # or minor / major
```

`publish` accepts exactly `patch`, `minor`, or `major`. It:

1. Requires a clean `main` (including no untracked files), the expected GitHub
   origin for both fetch and push, usable tools/authentication, license metadata
   and committed text, the `pypi` environment, and an enabled release workflow.
2. Fetches `origin/main` and tags. Local and remote main must match exactly;
   outdated, ahead, or diverged branches stop with instructions, without reset.
3. Rejects conflicting tags/releases, unfinished releases of the current version,
   and repeat releases with no new commits. Uses `uv version --bump … --dry-run
   --short` to compute the candidate and checks PyPI for version reuse. Network
   and authentication errors stop the operation; only a PyPI HTTP 404 means absent.
4. Runs `uv version --bump … --no-sync`, which updates the root version/lock, then
   `uv lock --project examples/wrapper`. Only that nested lock records PyCapsule;
   the unrelated fixture lock is checked but not regenerated.
5. Runs `just check build` against the bumped state. This checks formatting, lint,
   types, tests, locks, canonical examples, strict distribution metadata, and
   capsule execution through an isolated installed wheel.
6. Rechecks remote main, tag/release conflicts, and PyPI; stages only the three
   version/lock files; commits `chore: release v<version>`; creates annotated tag
   `v<version>`; atomically pushes main and that tag (without other local tags).
7. Runs `gh release create v<version> --repo christopher-caldwell/py_capsule
   --verify-tag --generate-notes`, immediately publishing the GitHub Release.
   The `release: types: [published]` event starts validation and PyPI upload.

For example, after 0.0.1 is published, `patch` produces 0.0.2, `minor` produces
0.1.0, and `major` produces 1.0.0. Choose major only when you intend a 1.0 release.

The current first version needs no bump: after merging the preparation PR and
completing setup, use **`just publish-initial`**. It accepts only 0.0.1 with no
previous GitHub releases/version tags, validates the already-committed revision,
and tags/pushes/releases it without an empty version commit. Regular `publish`
requires the current version to have a GitHub Release first.

## Failure and recovery

- Before validation succeeds, no release commit/tag/push occurs. Bump or validation
  failures deliberately leave version/lock changes for inspection. Fix the cause,
  inspect `git diff`, then restore only those mechanical changes before retrying
  the bump. The command never automatically discards work.
- If a commit exists but tag creation failed, inspect the release commit and
  version, resolve the tag/signing problem, and create that same annotated tag
  with `git tag -a v<version> -m "Release v<version>"`. Do not bump again.
- If an atomic push fails, local commit/tag remain. Resolve authentication/rules
  or the remote conflict; do not force-push. If the commit is still the correct
  successor of remote main, retry the exact operation:
  `git push --atomic --no-follow-tags origin main refs/tags/v<version>`.
  If remote main advanced, reconcile and revalidate before deciding which
  revision should be released. No automatic rollback rewrites either history.
- Once the intended commit/tag are safely on origin and local main is at that
  exact revision, run **`just publish-resume`**. It requires the current version's
  identical annotated tag at local/remote HEAD, rechecks prerequisites and
  validation, then creates the GitHub Release without bumping or committing.
  A published Release already present is a harmless no-op; a draft requires
  inspection and explicit publication on GitHub. Run recovery before merging
  further changes. Normal `publish` refuses to bump an unfinished current tag.
- If GitHub Actions fails or waits for an environment approval after the Release
  exists, inspect that release's run and approve/retry the appropriate job.
  `publish-resume` does not retrigger a published Release or upload artifacts.
  Do not delete/reuse a version already uploaded to PyPI.

## CI acceptance

PRs and manually dispatched runs validate without uploading. Release events run
that same pipeline plus the license/tag gate. Python 3.11–3.14 are tested. CI
also inspects archive contents, records SHA-256 hashes, tests a clean wheel
installation and canonical examples, rebuilds the sdist, and compares library
sources. The publish job downloads these exact artifacts and does not rebuild.
The pending `pypi` environment approval, if configured, remains a manual step.

After successful publication, verify from a separate project with
`uv add py-capsule==0.0.1` (substitute the released version on subsequent releases).
Remove any old Git/local source override and regenerate that consumer's lockfile.

## Tool choice

[Native uv versioning](https://docs.astral.sh/uv/guides/package/#updating-your-version)
already handles the one authoritative version and root lockfile.
[Bump My Version](https://github.com/callowayproject/bump-my-version) adds configurable
multi-file version replacement and Git automation that this project does not need.
[Commitizen](https://commitizen-tools.github.io/commitizen/) and
[Python semantic-release](https://python-semantic-release.readthedocs.io/en/latest/)
add commit conventions, changelogs, and automated version decisions.
[release-please](https://github.com/googleapis/release-please) maintains release PRs
from commit history. Here the owner explicitly chooses the bump after merging;
`uv + git + gh` fits that workflow with no extra version-management dependency.

The [gh release command](https://cli.github.com/manual/gh_release_create) publishes
immediately unless `--draft` is given; `--verify-tag` prevents implicit tag creation.
Its newer `--fail-on-no-commits` flag has no effect on the first release and is
unavailable in the installed gh 2.66.1. The local command instead rejects HEAD at
the current release tag before creating a mechanical version commit.
