# Ratel evals contract: evals/v1

> **Experimental preview.** evals/v1 may change.

The wire contract to start and read an eval run, and to compare it with a pinned baseline. It is apart from
[`sandbox/v1`](../../sandbox/v1), which does not know evals.

| Schema | Use |
|---|---|
| `start-experiment.request`, `start-experiment.response` | `POST /api/v1/evals/experiments` (permission `evals:write`) |
| `experiment` | `GET /api/v1/evals/experiments/{id}` (permission `evals:read`) |
| `pin-baseline.request`, `baseline` | `GET`, `PUT` and `DELETE /api/v1/evals/baseline` (`evals:read`, the others `evals:write`) |
| `comparison` | `GET /api/v1/evals/experiments/{id}/comparison` (`evals:read`) |
| `error` | The body of each refused call |

An experiment runs each approved case on one sandbox version. It freezes the case versions and their
scorers, and it never changes. A trial is `pass`, `fail` or `invalid`. An infrastructure failure is
`invalid`. The answers have raw counts and no captured text: the page at `url` shows the outputs and
the reasons to a member of the project.

A comparison matches the cases by id. A case is `pass` when all its valid trials pass, `fail` when a
valid trial fails, and `invalid` when no trial is valid. The verdict is `regressed` when a case goes
from pass to fail, else `inconclusive` when a compared case is invalid (or none is compared), else
`improved` when a case goes from fail to pass, else `unchanged`. A case of one side is `added` or
`removed` and does not decide. The refusals (409) are `no_baseline`, `same_experiment`,
`not_completed` and `scorers_differ`.

## Compatibility

A receiver ignores fields that it does not know. New optional fields are additive in v1. A change
to the meaning of a field needs a new major version.

## Conformance

`conformance/valid/<schema>.<case>.json` must pass `schema/<schema>.schema.json`, and
`conformance/invalid/<schema>.<case>.json` must fail it. The tests of
[`src/sandbox/ts`](../../../src/sandbox/ts) run them.
