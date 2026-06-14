# Releasing to PyPI

This page documents the end-to-end release procedure for maintainers.
Releases are automated by the tag-triggered
[`.github/workflows/publish.yml`](https://github.com/robotsix/robotsix-agent-comm/blob/main/.github/workflows/publish.yml)
workflow: a `vX.Y.Z` tag builds with `uv build`, validates with
`twine check`, and publishes to PyPI via **Trusted Publishing (OIDC)**
before cutting a CHANGELOG-driven GitHub Release. A
`workflow_dispatch` run provides a TestPyPI **dry-run** path.

## One-time operator setup

These steps require PyPI account access and GitHub repository-admin
rights. CI **cannot** perform them — they are manual, human steps.

1. **Create the GitHub Environments.** In the repository settings,
   create two environments named exactly `pypi` and `testpypi`. Add any
   desired protection rules (e.g. required reviewers for `pypi`).
2. **Configure the PyPI Trusted Publisher.** On
   [PyPI](https://pypi.org/manage/account/publishing/), add a new
   *pending* (or project) trusted publisher pointing at:
   - Repository: `robotsix-agent-comm` (owner `robotsix`)
   - Workflow: `publish.yml`
   - Environment: `pypi`
3. **Configure the TestPyPI Trusted Publisher.** Repeat the previous
   step on [TestPyPI](https://test.pypi.org/manage/account/publishing/)
   with the `testpypi` environment.

No secrets are stored anywhere: Trusted Publishing exchanges a
short-lived OIDC token at publish time.

## Per-release steps

1. **Update the changelog.** In `CHANGELOG.md`, move the accumulated
   `## [Unreleased]` entries into a new `## [X.Y.Z]` section and leave a
   fresh empty `Unreleased` section above it.
2. **Bump the version.** Set `[project].version` in `pyproject.toml` to
   `X.Y.Z`. This is the single source of truth for the version; the
   workflow's tag/version guard fails the build if the tag and this
   value disagree.
3. **Dry-run to TestPyPI.** Trigger the `Publish` workflow manually
   (`workflow_dispatch`) with the `testpypi` target and confirm the
   build, `twine check`, and TestPyPI upload all succeed.
4. **Tag and push.** Create and push the release tag:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

   The tag push triggers the real PyPI publish and creates a GitHub
   Release whose notes are the extracted `## [X.Y.Z]` changelog section.

## Credential model

**Trusted Publishing (OIDC) is the primary, mandated mechanism.** It
stores no token: the `publish-pypi` / `publish-testpypi` jobs request
`id-token: write` and `pypa/gh-action-pypi-publish` exchanges the OIDC
identity for a short-lived upload token scoped to the configured
environment.

**API-token fallback.** For environments where Trusted Publishing is
unavailable, store a scoped PyPI API token as the `PYPI_API_TOKEN`
GitHub Actions secret and pass it to the publish action:

```yaml
- uses: pypa/gh-action-pypi-publish@release/v1
  with:
    password: ${{ secrets.PYPI_API_TOKEN }}
```

This repository ships with Trusted Publishing wired and does **not**
store any token in the workflow YAML.

## Future work

The inline `publish.yml` is self-contained (mirrored into
`templates/python-package/`) so each repo owns a working pipeline.
Promoting it into a shared
`damien-robotsix/robotsix-mill/.github/workflows/python-publish.yml@main`
reusable workflow — alongside `python-ci.yml` and `python-docs.yml` —
is tracked as later epic work.
