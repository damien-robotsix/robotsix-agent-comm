# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security
vulnerabilities.

Instead, report them privately via GitHub's security advisory system:

- **Preferred:** [Private vulnerability reporting](https://github.com/damien-robotsix/robotsix-agent-comm/security/advisories/new)
- **Alternative:** Email the maintainer at [damien.robotsix@gmail.com](mailto:damien.robotsix@gmail.com)

## Disclosure Policy

The maintainer will acknowledge your report within 72 hours and will
collaborate with you on a fix before any public disclosure.

## Secrets Scanning

This repository uses [gitleaks](https://github.com/gitleaks/gitleaks) to
detect hardcoded secrets and credentials before they can be committed.

- **Pre-commit:** gitleaks runs as a pre-commit hook on every staged
    change, blocking commits that introduce potential secrets.
- **CI:** The same gitleaks configuration runs in CI as an additional
    safety net on pull requests.
- **Configuration:** Detection rules and path-based allowlists are defined
    in `.gitleaks.toml`. Test fixtures, examples, and generated templates
    are excluded from scanning via path allowlists.
- **Baseline:** False positives that cannot be addressed structurally
    should be recorded in `.gitleaks.toml` allowlist entries rather than an
    external baseline file.

If gitleaks blocks a legitimate secret-like pattern (e.g. test fixtures
or documentation examples), update the `.gitleaks.toml` allowlist with
the appropriate path or rule exception rather than bypassing the hook.
