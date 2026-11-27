# Changelog

All notable changes to `@ratel-ai/cli` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this package adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `ratel push`: validate `ratel.yaml`, build (or take) the Docker image of an agent, upload it to Ratel as a gzip tarball with the parsed `ratel.yaml`, and wait for the smoke check of the version.
- `ratel run --local`: start the image with local Docker and send one request per the run contract `protocol/sandbox/v1` (HTTP mode or command mode). A contract error prints the container logs and the exact error.
- `ratel run`: run the newest ready version of the agent on Ratel and print the output.
- `ratel eval run` starts one run for each eval (`POST /evals/runs`) and prints one document with a part for each eval. New: `--dataset <name>`, `--param name=a,b` for a matrix of at most 12 combinations, and `--version` applies only to the evals of the sandbox of that version. The exit codes do not change: with more evals the worst part gives the code.
- `ratel eval run` and `ratel eval baseline pin`, with the exit codes and the `--json` document of the earlier `@ratel-ai/sandbox` CLI. New: `--version` (an image version), `--param name=value` checked against `ratel.yaml`, `--label` and `--name`. The address is `RATEL_BASE_URL` (the API root). `--timeout` ends the wait with exit 4 and `wait_timeout`.
