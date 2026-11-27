# Changelog

All notable changes to `@ratel-ai/cli` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this package adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `ratel push`: validate `ratel.yaml`, build (or take) the Docker image of an agent, upload it to Ratel as a gzip tarball with the parsed `ratel.yaml`, and wait for the smoke check of the version.
- `ratel run --local`: start the image with local Docker and send one request per the run contract `protocol/sandbox/v1` (HTTP mode or command mode). A contract error prints the container logs and the exact error.
- `ratel run`: run the newest ready version of the agent on Ratel and print the output.
- `ratel eval run` and `ratel eval baseline pin`, with the exit codes and the `--json` document of the earlier `@ratel-ai/sandbox` CLI. New: `--version` (an image version), `--param name=value` checked against `ratel.yaml`, `--label` and `--name`. The address is `RATEL_BASE_URL` (the API root). `--timeout` ends the wait with exit 4 and `wait_timeout`.
- `ratel eval run` starts one run for each eval (`POST /evals/runs`), or one run of each `--eval` (one name: `POST /evals/{eval}/runs`), and waits for each. The exit codes do not change: with more runs, the exit code is the highest of the runs (a refused eval is 3). `--json` of one run is the document of one run, as before (with the additive `note` and the new fields of the experiment). With more runs it is `{ "v": 1, "runs": [{ eval, evalId, experimentId, url, note?, exitCode, score, counts, delta, variants, comparison? } or { eval, evalId, error, message, exitCode }], "exitCode" }`.
- `ratel eval run --dataset <name|id>`: the cases of a dataset in place of the cases of each eval, for this run only.
- `ratel eval run --param name=a,b`: a value with commas is a list, sent as `matrix`. Each combination is a variant of the run. More than 12 combinations exit with 3 before any call. The text prints the score of the run and one line for each variant.
- `ratel eval run --compare-baseline` compares each run with the baseline of its eval, and checks the baseline of each eval (`GET /evals/baseline?eval=<evalId>`) before it starts a run.

### Fixed

- `ratel eval run --version <id>` of one sandbox no longer applies to an eval of another sandbox: that eval runs on its own sandbox (Ratel decides), and the command prints the `note` of Ratel. It is not a refusal.
