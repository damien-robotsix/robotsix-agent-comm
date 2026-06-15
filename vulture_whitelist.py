# vulture whitelist — list symbols that vulture would flag as dead code
# but that are actually used (e.g. re-exports, public API, entry points).
# Entries below are bare expressions — ruff B018 is suppressed per-file
# in pyproject.toml.
