# Pre-publication checklist

Copy this list into the publishing ticket and tick each gate before
publishing a repository to PyPI. Every box MUST be checked.

- [ ] **Project structure & layout** — package lives under
    `src/<package>/`, `tests/` mirrors it, build backend is Hatchling,
    and a committed `uv.lock` is present.
- [ ] **Required metadata** — `pyproject.toml` `[project]` declares
    `name`, `version`, `description`, `readme`, `requires-python`
    (`>=3.12`), `license`, `authors`, `dependencies`, and
    `classifiers` (OSI license, `Programming Language :: Python :: 3.x`,
    `Typing :: Typed`, accurate `Development Status`).
- [ ] **README & documentation** — top-level `README.md` (overview,
    status, install/dev, contributing pointer) and a mkdocs +
    mkdocs-material + mkdocstrings `docs/` site with every page wired
    into `mkdocs.yml` `nav`.
- [ ] **Version numbering** — version follows Semantic Versioning with a
    single source of truth in the git tag (hatch-vcs derives the
    version at build time).
- [ ] **Changelog** — `CHANGELOG.md` present in repo root, follows Keep
    a Changelog, has an `Unreleased` section, and is updated for this
    release.
- [ ] **License file** — `LICENSE` present in repo root and matches
    `pyproject.toml` `[project].license`.
- [ ] **Contributing guidelines** — `CONTRIBUTING.md` covers dev setup
    (`uv sync`), check commands (`ruff check`, `ruff format --check`,
    `mypy`, `pytest`), branch/PR flow, and ADR location.
- [ ] **Test coverage / CI** — CI is green on push/PR running ruff (lint
    \+ format check), mypy `strict`, and pytest with branch coverage at
    or above the 85% threshold, with `filterwarnings = ["error"]`.
- [ ] **Automated release pipeline** — `.github/workflows/publish.yml`
    publishes on `vX.Y.Z` tags via Trusted Publishing (OIDC, no stored
    secret), with a TestPyPI dry-run path, a tag/version guard, and a
    CHANGELOG-driven GitHub Release.
- [ ] **Initial release executed & verified** — the release has been
    published to PyPI and verified per
    [`releasing.md` › Post-publish verification](releasing.md#post-publish-verification):
    the project page is live/indexed, a clean-venv `pip install` plus
    import smoke check succeeds, and the `vX.Y.Z` git tag and
    auto-generated GitHub Release are present. (Operator / network
    step.)
