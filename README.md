# Ratel SDKs

The client libraries of [Ratel](https://www.ratel.sh): the Rust core, the TypeScript and Python
SDKs, their framework adapters, the rankers plugins, the telemetry helpers and the Cloud client.
The platform (console and API) lives in [ratel-ai/ratel](https://github.com/ratel-ai/ratel).

## Packages

| Package | Registry | Path |
| --- | --- | --- |
| `ratel-ai-core` | crates.io | `src/core` |
| `ratel-ai-telemetry` | crates.io | `src/telemetry/core` |
| `ratel-ai-rankers` | crates.io | `src/rankers/core` |
| `@ratel-ai/sdk` | npm | `src/sdk/ts` |
| `@ratel-ai/vercel-ai-sdk` | npm | `src/adapters/ts-vercel-ai-sdk` |
| `@ratel-ai/mastra` | npm | `src/adapters/ts-mastra` |
| `@ratel-ai/telemetry` | npm | `src/telemetry/ts` |
| `@ratel-ai/rankers-plugin` | npm | `src/rankers/ts` |
| `@ratel-ai/cloud-sdk` | npm | `src/cloud-sdk/ts` |
| `ratel-ai` | PyPI | `src/sdk/python` |
| `ratel-ai-telemetry` | PyPI | `src/telemetry/python` |
| `ratel-ai-rankers-plugin` | PyPI | `src/rankers/python` |

`protocol/` holds the wire contracts and their conformance vectors. `examples/` holds runnable
examples for each SDK. `packages/sdk/` holds the SDK docs (`llms.txt`).

## History

This repository was split out of `ratel-ai/ratel` with `git filter-repo`, so `git log` keeps the
history of every moved path. `@ratel-ai/cloud-sdk` came from `ratel-ai/ratel-cloud-sdk` as one
squashed commit.

## Develop

Requirements: Rust stable, Node 24 with pnpm 10, Python 3.11 with `uv`.

```sh
pnpm install
pnpm build && pnpm test          # TypeScript packages
cargo test --workspace           # Rust crates
```

The Python packages build with `maturin`; see `.github/workflows/ci.yml` for the exact steps.

## Contribute

PR titles are conventional commits: see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

See `LICENSE.md` and `LICENSE-APACHE`.
