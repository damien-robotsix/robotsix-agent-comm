# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-06-14

### Added

- Core messaging protocol layer (`robotsix_agent_comm.protocol`):
  message types, JSON serialization, and validation.
- Network transport layer (`robotsix_agent_comm.transport`): HTTP/JSON
  client and server, agent registry, router, retry policy, and endpoint
  definitions.
- High-level synchronous SDK (`robotsix_agent_comm.sdk`) exposing the
  `Agent` client, with runnable examples under `examples/` and tutorial
  documentation.
