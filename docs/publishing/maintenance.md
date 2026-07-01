# Package maintenance procedures

This page is the maintainer runbook for the **ongoing lifecycle** of a
package after it has been published to PyPI. It complements the
companion publishing docs:

- [PyPI publishing standards](index.md) — the fleet-wide quality bar a
    repo must meet before it is published.
- [Pre-publication checklist](checklist.md) — the one-time gate ticked
    through before the first publish.
- [Releasing to PyPI](releasing.md) — the step-by-step release
    mechanics (one-time operator setup, tag/version guard, TestPyPI
    dry-run, `gh release create`, post-publish verification).

This runbook does **not** restate those release mechanics — it
cross-links to `releasing.md` for them and focuses on the policies that
keep a published package healthy over time. As with the rest of this
set, PyPI/GitHub-admin actions (yanking, archiving, publishing security
advisories) are **operator steps** — documented here, performed by a
human with the necessary access.

## Versioning & release cadence policy

Versions follow [Semantic Versioning](https://semver.org/)
(`MAJOR.MINOR.PATCH`), as required by the
[Version numbering](index.md#version-numbering) section of the
standards. Decide the bump from the nature of the change:

- **PATCH** (`x.y.Z`) — backwards-compatible bug fixes, internal
    refactors, doc-only corrections, dependency bumps with no API impact.
- **MINOR** (`x.Y.0`) — backwards-compatible new functionality:
    added public API, new optional parameters with defaults, new
    deprecations (the deprecated thing still works).
- **MAJOR** (`X.0.0`) — any backwards-incompatible change: removing or
    renaming public API, changing a signature/return type, tightening
    accepted input, or removing a previously deprecated symbol.

There is **no fixed calendar cadence**: release when there is something
worth releasing. Batch small fixes into a periodic PATCH rather than
tagging every commit; ship MINOR features when they are documented and
tested; reserve MAJOR for genuinely breaking work and announce it (see
[Communication plan for major releases](#communication-plan-for-major-releases)).

**Version is single-source-of-truth.** The only authoritative version
is the git tag (`vX.Y.Z`); `hatch-vcs` derives the version from the tag
at build time and `pyproject.toml` declares `dynamic = ["version"]`
with `[tool.hatch.version] source = "vcs"`. On each release the
accumulated `## [Unreleased]`
entries in [`CHANGELOG.md`](../../CHANGELOG.md) are rolled up into a new
`## [X.Y.Z]` section and a fresh empty `Unreleased` section is left
above it. See [Releasing to PyPI](releasing.md#per-release-steps) for
the exact mechanics — do not duplicate them here.

**Pre-1.0 maturity signalling.** Until the public API has stabilized,
keep the package pre-1.0 and signal maturity through an accurate
`Development Status` classifier (e.g. `2 - Pre-Alpha`,
`3 - Alpha`, `4 - Beta`) rather than prematurely cutting `1.0.0`.
Bumping to `1.0.0` is a deliberate commitment that the public API is
stable and that breaking it requires a MAJOR release.

## Per-release checklist template

Paste the block below into each release PR or tracking issue and tick
it through. This is the **recurring per-release** list; it is distinct
from the one-time [pre-publication gate](checklist.md), and it stays
consistent with the mechanics in [Releasing to PyPI](releasing.md).

```markdown
## Release vX.Y.Z

- [ ] Decide the bump (PATCH / MINOR / MAJOR) per SemVer
- [ ] Roll up `CHANGELOG.md` `[Unreleased]` into `## [X.Y.Z]`; leave a fresh empty `[Unreleased]`
- [ ] No version bump needed — `hatch-vcs` derives the version from the git tag
- [ ] `uv lock` clean (no unintended dependency changes)
- [ ] Local checks green: `ruff check .`, `ruff format --check .`, `mypy .`, `pytest`
- [ ] Dry-run to TestPyPI via `workflow_dispatch` (build + `twine check` + upload OK)
- [ ] Tag and push `vX.Y.Z` (triggers the real PyPI publish)
- [ ] Verify clean install from PyPI into a fresh venv and import smoke check
- [ ] Confirm the CHANGELOG-driven GitHub Release was cut
- [ ] For MAJOR/notable releases: announcement + migration note (see below)
```

## Breaking changes & deprecations

A breaking change is any backwards-incompatible change to the public
API and **requires a MAJOR bump** (pre-1.0, treat a MINOR bump as the
breaking-change vehicle and call it out loudly in the CHANGELOG).

Prefer the **warn-before-remove** deprecation path over abrupt removal:

1. **Warn.** When introducing the replacement, keep the old behaviour
    working and emit a `DeprecationWarning` from the deprecated code
    path, pointing at the replacement:

    ```python
    import warnings

    def old_api(...):
        warnings.warn(
            "old_api() is deprecated; use new_api() instead. "
            "It will be removed in a future major release.",
            DeprecationWarning,
            stacklevel=2,
        )
        ...
    ```

2. **Document.** Record the deprecation in `CHANGELOG.md` under a
    `Deprecated` heading, naming the symbol and its replacement.

3. **Keep for ≥1 minor release.** Leave the deprecated symbol in place
    for at least one minor release before removing it, so downstream
    users have a version where both the warning and the replacement
    coexist.

4. **Remove.** Only then remove the symbol — that removal is a breaking
    change and goes under `Removed` in the CHANGELOG with the
    corresponding MAJOR bump.

**Interaction with `filterwarnings = ["error"]`.** The test config
([`pyproject.toml`](../../pyproject.toml)) turns warnings into errors,
so a freshly added `DeprecationWarning` will fail the suite unless it is
handled. Test the deprecation explicitly rather than silencing it
globally — assert that the warning fires and that the path still works:

```python
import pytest

def test_old_api_warns():
    with pytest.warns(DeprecationWarning):
        old_api(...)
```

Internal callers should be migrated to the replacement so they do not
themselves trip the warning-as-error config. Avoid blanket
`filterwarnings` ignores; scope any suppression to the specific
deprecation under test.

## Communication plan for major releases

Every MAJOR release — and any otherwise notable MINOR — is announced so
downstream users are not surprised:

- **GitHub Release notes.** The release workflow cuts a GitHub Release
    whose notes are the extracted `## [X.Y.Z]` CHANGELOG section
    (see [Releasing to PyPI](releasing.md#per-release-steps)). Make sure
    that section reads as user-facing release notes, not terse internal
    shorthand.
- **README status update.** Update the status/badge line in
    [`README.md`](../../README.md) (and the `Development Status`
    classifier if maturity changed) so the front door reflects the new
    version.
- **Migration note.** For breaking releases, include a short migration
    note in the CHANGELOG `## [X.Y.Z]` section (and/or the GitHub Release)
    that lists: what changed and why, the before/after for each affected
    API, the deprecation timeline if applicable, and a minimal upgrade
    example. The goal is that a user can upgrade by following the note
    without reading the diff.

## Long-term maintenance expectations

A published package carries an ongoing maintenance commitment:

- **Supported Python versions.** Track `requires-python = ">=3.12"` and
    keep the `Programming Language :: Python :: 3.x` classifiers in
    `pyproject.toml` accurate. When adding support for a newer Python,
    add its classifier and exercise it in CI; when dropping an
    end-of-life version, that is a breaking change (bump
    `requires-python`, drop the classifier, MAJOR/MINOR as appropriate)
    and is announced like any other.
- **Dependency & lockfile upkeep.** Keep dependencies current and the
    committed `uv.lock` reproducible. Regenerate with `uv lock` (never
    hand-edit the lockfile) and commit the result; review dependency
    bumps for API impact before release.
- **CI stays green.** Every push and pull request must pass the shared
    reusable workflow
    `damien-robotsix/robotsix-mill/.github/workflows/python-ci.yml@main`
    (ruff lint + format, `mypy --strict`, pytest with the fleet coverage
    threshold). A red `main` blocks releasing — fix it first.
- **Issue triage cadence.** Triage incoming issues and pull requests
    promptly: acknowledge, label by severity, and either schedule a fix
    into the next release or explain why it is out of scope. Security
    reports follow the dedicated flow below.

## Handling security vulnerabilities

Security issues are handled privately and on an expedited timeline:

1. **Private reporting channel.** Accept reports through GitHub's
    private vulnerability reporting (Security → "Report a vulnerability")
    rather than public issues, so a fix can ship before disclosure.
    Direct reporters there from the README/SECURITY policy.
2. **Triage & severity.** Confirm the report, assess severity and
    affected versions (CVSS-style: impact × exploitability), and
    reproduce privately. Decide the lowest release type that ships the
    fix safely.
3. **Coordinated fix, then release.** Develop the fix on a private
    branch, then publish an **expedited PATCH** (or MINOR if the fix
    needs an additive API change) through the normal
    [release flow](releasing.md#per-release-steps). Do not disclose
    details until the fixed version is on PyPI.
4. **Publish a GitHub Security Advisory (GHSA).** Publish the advisory
    (and request a CVE if warranted) once the fix is released, crediting
    the reporter. **Creating/publishing the GHSA is an operator step** —
    it requires repo-admin access.
5. **CHANGELOG.** Note the fix in `CHANGELOG.md` under a `Security`
    heading, referencing the advisory ID once published.

## Deprecating / retiring a package

Occasionally a published package should be sunset — e.g. it has been
superseded, merged into another package, or is no longer maintained.

**Criteria.** Consider retirement when the package is superseded by a
replacement, the functionality moved elsewhere, there is no maintainer
willing to keep CI green, or the project no longer fits the fleet's
direction.

**Steps:**

1. **Cut a final release with a deprecation notice.** Add a clear
    deprecation/sunset notice at the top of `README.md` and a
    `Deprecated` entry in `CHANGELOG.md` (point to the replacement and
    the end-of-maintenance date), then publish that final version
    through the normal [release flow](releasing.md).
2. **Archive the repository.** Set the GitHub repo to archived
    (read-only) so it is clearly unmaintained. **Operator step**
    (repo-admin).
3. **PyPI-side actions.** Optionally mark the PyPI project as
    inactive/deprecated, or yank specific releases that are known-broken
    (yanking hides a release from new resolutions without deleting it).
    **These are operator steps** requiring PyPI account access — document
    the intent here; a human performs them.

Retiring a package is itself a notable event — announce it via the
final GitHub Release notes and README, as in the
[communication plan](#communication-plan-for-major-releases).

## Future work

The copy-and-rename [`templates/python-package/`](https://github.com/robotsix/robotsix-agent-comm/tree/main/templates/python-package)
skeleton has no `docs/` directory, so this runbook is not mirrored into
the template today. If a future template gains a `docs/` skeleton, a
maintenance-doc stub should be added there to match this reference
implementation.
