# AGENTS.md: working in ratel-sdk

Read [README.md](README.md) for setup and [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution
rules.

## Pull request titles

A PR title **MUST** be a conventional commit: `type(scope)!: description`, with an optional scope
and `!`. Types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `revert`. The `pr-title` check fails any other
title, and the title becomes the squash commit on `main`. Put a task key in the scope
(`feat(RP-123): ...`) or in the PR body, never as the prefix.
