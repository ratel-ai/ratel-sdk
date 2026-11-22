# ratel-sdk repo-move release: proof

Release commit: `6f819fc3bb6eef1271d0a837413ab33353a38424` (merge of #13). Registry output: [registries.log](registries.log).

## Release tags and release.yml runs

| # | Unit | Tag | release.yml run | Result |
|---|---|---|---|---|
| 1 | telemetry-core | `telemetry-core-v0.4.1` | [45059139947](https://github.com/ratel-ai/ratel-sdk/actions/runs/45059139947/attempts/4) | success on attempt 4 (RP-162); attempts 1-3 failed, see Retries |
| 2 | telemetry-ts | `telemetry-ts-v0.5.1` | [45059282688](https://github.com/ratel-ai/ratel-sdk/actions/runs/45059282688) | success |
| 3 | telemetry-py | `telemetry-py-v0.5.1` | [45059574078](https://github.com/ratel-ai/ratel-sdk/actions/runs/45059574078) | success |
| 4 | core | `core-v0.12.0-rc.7` | [45070102410](https://github.com/ratel-ai/ratel-sdk/actions/runs/45070102410) | success (tag pushed by RP-162) |
| 5 | sdk-ts | `sdk-ts-v0.13.0-rc.13` | [45059688024](https://github.com/ratel-ai/ratel-sdk/actions/runs/45059688024) | success |
| 6 | sdk-py | `sdk-py-v0.13.0-rc.13` | [45060661929](https://github.com/ratel-ai/ratel-sdk/actions/runs/45060661929) | success |
| 7 | vercel-ai-sdk | `vercel-ai-sdk-v0.6.0-rc.2` | [45061220000](https://github.com/ratel-ai/ratel-sdk/actions/runs/45061220000) | success |
| 8 | mastra | `mastra-v0.5.0-rc.2` | [45061704569](https://github.com/ratel-ai/ratel-sdk/actions/runs/45061704569) | success |
| 9 | rankers-ts | `rankers-ts-v0.1.0-rc.2` | [45062298588](https://github.com/ratel-ai/ratel-sdk/actions/runs/45062298588) | success |
| 10 | rankers-py | `rankers-py-v0.1.0-rc.2` | [45063048237](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063048237) | failed: PyPI 403. Ignored by owner for now (not retried) |
| 11 | cloud-sdk | `cloud-sdk-v0.7.0-rc.5` | [45063343001](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063343001) | success |

## verify-install.yml (dispatched per unit at the new version)

| Unit | Version | Run | Result |
|---|---|---|---|
| telemetry-core | 0.4.1 | [45070289124](https://github.com/ratel-ai/ratel-sdk/actions/runs/45070289124) | success |
| core | 0.12.0-rc.7 | [45070293479](https://github.com/ratel-ai/ratel-sdk/actions/runs/45070293479) | success |
| telemetry-ts | 0.5.1 | [45063568810](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063568810) | success |
| telemetry-py | 0.5.1 | [45063580334](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063580334) | success |
| sdk-ts | 0.13.0-rc.13 | [45063591181](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063591181) | success |
| sdk-py | 0.13.0rc13 | [45063602320](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063602320) | success |
| vercel-ai-sdk | 0.6.0-rc.2 | [45063614064](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063614064) | success |
| mastra | 0.5.0-rc.2 | [45063626230](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063626230) | success |
| rankers-ts | 0.1.0-rc.2 | [45063638406](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063638406) | success |
| cloud-sdk | 0.7.0-rc.5 | not a verify-install unit | clean `npm install` + import OK ([cloud-sdk-install.log](cloud-sdk-install.log)) |

## Crates published (RP-162, 2026-11-22)

crates.io now trusts `ratel-ai/ratel-sdk`. Both crates were published by Trusted Publishing from commit 6f819fc3:

| Crate | Version | crates.io repository | Published | trustpub run |
|---|---|---|---|---|
| ratel-ai-telemetry | 0.4.1 | https://github.com/ratel-ai/ratel-sdk | 21:04:56 UTC | 45059139947 |
| ratel-ai-core | 0.12.0-rc.7 | https://github.com/ratel-ai/ratel-sdk | 21:06:54 UTC | 45070102410 |

rankers-py (`ratel-ai-rankers-plugin` 0.1.0rc2 on PyPI) is still unpublished. The owner said to ignore it for now.

## Retries (RP-159, RP-161): publishers still refused

The owner reported the three Trusted Publishers as added (20:30 UTC) and then as fixed (20:45 UTC, RP-160). Each time, both failed jobs were re-run, and both failed again with the same registry errors:

| Run | Attempt | Time (UTC) | Exact registry error |
|---|---|---|---|
| [45059139947](https://github.com/ratel-ai/ratel-sdk/actions/runs/45059139947/attempts/2) (telemetry-core 0.4.1, crates.io) | 2 | 20:30 | `Failed to retrieve token from Cargo registry. Status: 400. Error: No Trusted Publishing config found for repository \`ratel-ai/ratel-sdk\`.` |
| [45059139947](https://github.com/ratel-ai/ratel-sdk/actions/runs/45059139947/attempts/3) (telemetry-core 0.4.1, crates.io) | 3 | 20:46 | same as attempt 2 |
| [45063048237](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063048237/attempts/2) (rankers-py 0.1.0rc2, PyPI) | 2 | 20:31 | `403 Invalid API Token: OIDC scoped token is not valid for project 'ratel-ai-rankers-plugin'` |
| [45063048237](https://github.com/ratel-ai/ratel-sdk/actions/runs/45063048237/attempts/3) (rankers-py 0.1.0rc2, PyPI) | 3 | 20:47 | same as attempt 2 |

`core-v0.12.0-rc.7` stays unpushed: it waits for telemetry-core on crates.io, and its crate uses the same publisher setup.

Status at RP-161 (superseded by RP-162 above): the three units were still unpublished. crates.io still finds no config for this repository. Check that the entry is on the crate's own Settings > Trusted Publishing page, under the `ratel-ai` GitHub owner, and not on a different crate. On PyPI, check that the entry is on the existing project `ratel-ai-rankers-plugin`, not a pending publisher, with the same repo, workflow and environment values.
