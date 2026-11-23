# Contributing to the Ratel SDKs

Setup and commands are in the [README](README.md); releases are in [RELEASING.md](RELEASING.md).

## Pull request titles

- A pull request title **MUST** be a [conventional commit](https://www.conventionalcommits.org):
  `type(scope)!: description`. The scope and the `!` (breaking change) are optional. The allowed
  types are `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `revert`.
- A task key goes in the scope (`feat(RP-123): ...`) or in the PR body, never as the prefix.
- A pull request merges as one squash commit and its title becomes the commit title. The
  `pr-title` check (`.github/workflows/pr-title.yml`) fails any other title. Commits inside the
  branch are free-form.
