# Ratel evals contract: evals/v1

> **Experimental preview.** evals/v1 may change.

The wire contract to start and read an eval run, and to compare it with a pinned baseline. It is apart from
[`sandbox/v1`](../../sandbox/v1), which does not know evals.

| Schema | Use |
|---|---|
| `start-experiment.request`, `start-experiment.response` | `POST /evals/experiments` (permission `evals:write`) |
| `experiment` | `GET /evals/experiments/{id}` (permission `evals:read`) |
| `pin-baseline.request`, `baseline` | `GET`, `PUT` and `DELETE /evals/baseline` (`evals:read`, the others `evals:write`) |
| `comparison` | `GET /evals/experiments/{id}/comparison` (`evals:read`) |

The paths are under `RATEL_BASE_URL`, the API root with `/api/v1`.
| `error` | The body of each refused call |

An experiment runs the suite, each eval of one sandbox with each of its cases (a case counts at once: there is no approval), on one image
version (a `version` of [`sandbox/v1`](../../sandbox/v1)), or on the newest ready version of that sandbox (`latest`).
`sandbox` names the sandbox (the `name` of `ratel.yaml`). Without it, `latest` takes the sandbox of the evals
named, or else the sandbox of the newest ready version. A version of another sandbox, and evals of
more than one sandbox, are refused with `wrong_sandbox` (409).
`evals` runs only the evals with these names (1 to 100). An unknown name is refused with
`not_found` (404), and an eval of another agent with `wrong_sandbox` (409). `trials` is the trials
of each case. Without it, each eval has its own trials (3 unless the eval sets another). `params` holds the
parameter values of each run: a flat object of the parameters that `ratel.yaml` declares. `label`
runs only the cases with that label, and `name` names the experiment. It freezes the case versions and their
scorers, and it never changes. A trial is `pass`, `fail` or `invalid`. An infrastructure failure is
`invalid`. The answers have raw counts and no captured text: the page at `url` shows the outputs and
the reasons to a member of the project.

A read of the suite also has `evals`, with or without `evals` in the request: each eval with its `counts`, its scorers and its `score`, the
mean pass rate of its scorers over the valid trials (0 to 1, null with no valid trial). `delta` is
the score minus the score of the same eval in the pinned baseline run (`baselineId`). When there is
no delta, `deltaRefusal` says why: `not_in_baseline` or `scorers_differ`. `verdict` is the verdict
of the run against the baseline, with the rule of the comparison, or null with no baseline.

A comparison matches the cases by id. A case is `pass` when all its valid trials pass, `fail` when a
valid trial fails, and `invalid` when no trial is valid. The verdict is `regressed` when a case goes
from pass to fail, else `inconclusive` when a compared case is invalid (or none is compared), else
`improved` when a case goes from fail to pass, else `unchanged`. A case of one side is `added` or
`removed` and does not decide. A case is `excluded` when the scorers of its eval differ between the
two runs: it does not decide, and the comparison can still have a verdict. The refusals (409) are
`no_baseline`, `same_experiment`, `not_completed` and `scorers_differ`.

## Compatibility

A receiver ignores fields that it does not know, and it accepts a `change` value that it does not
know. New optional fields are additive in v1. A change to the meaning of a field needs a new major
version. One exception: before evals, an absent `trials` meant 3 trials for each case. Now it means
the trials of each eval, which are 3 unless the eval sets another. A sender that needs a fixed
number gives `trials`.

## Conformance

`conformance/vectors.json` lists valid and invalid documents for each schema. An invalid vector
names the JSON pointer of its first error. `node protocol/evals/v1/verify.mjs` checks them with the
validator of `sandbox/v1` (Node built-ins only), and the CI job `protocol` runs it.
