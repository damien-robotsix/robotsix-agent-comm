# 1. Stdlib-first, minimal dependencies

- Status: Accepted
- Date: 2026-06-14

## Context

`robotsix-agent-comm` is part of the robotsix ecosystem, which follows a
stdlib-first engineering philosophy (fleet ADR 0001). The agent
communication system will, in later phases, grow a messaging protocol,
serialization, and a transport layer — areas where it is tempting to
reach for third-party libraries.

## Decision

This repository prefers the Python standard library over third-party
runtime dependencies. New runtime dependencies are added only when the
standard library genuinely cannot meet the need, and each such addition
must be justified.

- Runtime dependencies are kept empty unless and until a concrete need
    is demonstrated.
- Development tooling (`ruff`, `mypy`, `pytest`) is allowed and managed
    in the dev dependency group.
- `uv` manages the environment and a committed `uv.lock` pins all
    versions for reproducible installs (`uv sync --frozen`).

## Consequences

- Future serialization and protocol decisions start from the assumption
    that stdlib facilities (e.g. `json`, `struct`, `asyncio`) are the
    default choice.
- Any proposal to add a runtime dependency should reference and update
    this decision.
- The dependency surface stays small, which simplifies security review
    and long-term maintenance.
