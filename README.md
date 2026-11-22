# ratel-sdk repo-move release: proof

Release commit: `6f819fc3bb6eef1271d0a837413ab33353a38424` (merge of #13). Registry output: [registries.log](registries.log).

## Release tags and release.yml runs

| # | Unit | Tag | release.yml run | Result |
|---|---|---|---|---|
| 1 | telemetry-core | `telemetry-core-v0.4.1` | [45059139947](https://github.com/ratel-ai/ratel-sdk/actions/runs/45059139947) | failed: crates.io has no Trusted Publisher for ratel-ai/ratel-sdk |
| 2 | telemetry-ts | `telemetry-ts-v0.5.1` | [45059282688](https://github.com/ratel-ai/ratel-sdk/actions/runs/45059282688) | success |
| 3 | telemetry-py | `telemetry-py-v0.5.1` | [45059574078](https://github.com/ratel-ai/ratel-sdk/actions/runs/45059574078) | success |
| 4 | core | `core-v0.12.0-rc.7` | not pushed | blocked: same crates.io Trusted Publisher gap |
| 5 | sdk-ts | `sdk-ts-v0.13.0-rc.13` | [45059688024](https://github.com/ratel-ai/ratel-sdk/actions/runs/45059688024) | success |
| 6 | sdk-py | `sdk-py-v0.13.0-rc.13` | [45060661929](https://github.com/ratel-ai/ratel-sdk/actions/runs/45060661929) | success |
| 7 | vercel-ai-sdk | `vercel-ai-sdk-v0.6.0-rc.2` | [45061220000](https://github.com/ratel-ai/ratel-sdk/actions/runs/45061220000) | success |
| 8 | mastra | `mastra-v0.5.0-rc.2` | [45061704569](https://github.com/ratel-ai/ratel-sdk/actions/runs/45061704569) | success |
| 9 | rankers-ts | `rankers-ts-v0.1.0-rc.2` | [45062298588](https://github.com/ratel-ai/ratel-sdk/actions/runs/45062298588) | success |
| 10 | rankers-py | `rankers-py-v0.1.0-rc.2` | [45063048237](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063048237) | failed: PyPI 403, no Trusted Publisher for ratel-ai/ratel-sdk on ratel-ai-rankers-plugin |
| 11 | cloud-sdk | `cloud-sdk-v0.7.0-rc.5` | [45063343001](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063343001) | success |

## verify-install.yml (dispatched per unit at the new version)

| Unit | Version | Run | Result |
|---|---|---|---|
| telemetry-ts | 0.5.1 | [45063568810](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063568810) | success |
| telemetry-py | 0.5.1 | [45063580334](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063580334) | success |
| sdk-ts | 0.13.0-rc.13 | [45063591181](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063591181) | success |
| sdk-py | 0.13.0rc13 | [45063602320](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063602320) | success |
| vercel-ai-sdk | 0.6.0-rc.2 | [45063614064](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063614064) | success |
| mastra | 0.5.0-rc.2 | [45063626230](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063626230) | success |
| rankers-ts | 0.1.0-rc.2 | [45063638406](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063638406) | success |
| cloud-sdk | 0.7.0-rc.5 | not a verify-install unit | clean `npm install` + import OK ([cloud-sdk-install.log](cloud-sdk-install.log)) |

## Open: owner action

Add a Trusted Publisher (repo `ratel-ai/ratel-sdk`, workflow `release.yml`, environment `release`) on:

- crates.io `ratel-ai-telemetry`: then `gh run rerun 45059139947 --failed`
- crates.io `ratel-ai-core`: then push `core-v0.12.0-rc.7` on 6f819fc3
- PyPI `ratel-ai-rankers-plugin`: then `gh run rerun 45063048237 --failed`

Then dispatch verify-install for telemetry-core 0.4.1, core 0.12.0-rc.7 and rankers-py 0.1.0rc2.

## Retry 2026-11-22 20:30 UTC (RP-159)

RP-158 was marked done, so both failed jobs were re-run (attempt 2). Both failed again with the same errors:

- [45059139947 attempt 2](https://github.com/ratel-ai/ratel-sdk/actions/runs/45059139947/attempts/2): crates.io `No Trusted Publishing config found for repository ratel-ai/ratel-sdk` (ratel-ai-telemetry 0.4.1).
- [45063048237 attempt 2](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063048237/attempts/2): PyPI 403 `OIDC scoped token is not valid for project 'ratel-ai-rankers-plugin'` (0.1.0rc2).
- `core-v0.12.0-rc.7` stays unpushed.

The owner chose "Not now". The three units stay unpublished until the Trusted Publishers above exist. Then re-run both jobs again and push the core tag.
