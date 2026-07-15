# Startup performance context

## Purpose and scope

Startup is measured as separate boundaries: service startup, service readiness, launcher preparation, proxy readiness, and command useful output or completion. Cold service boots and commands against an already-running service are intentionally not combined. Docker and Helm cold-start timing remains out of scope until those environments are run directly.

## Decisions and constraints

- Durations use a monotonic clock; wall time is attribution metadata only.
- Historical runs are JSONL artifacts rather than database writes on the startup path.
- Records keep a stable label and executable name, but omit raw arguments, prompts, credentials, and environment values.
- Alembic-head validation remains fail closed. Full schema drift validation is explicit at startup and remains mandatory in `agent-lb-db check`.
- Interactive `cc` defers account enrichment until first use; headless `cc` keeps eager deterministic selection.
- `ccgpt` proves both protocol capability and a routable OpenAI pool. The launcher supports either endpoint order. The deployed laptop posture uses its federated access-token mirror first for startup speed and Studio second as fallback; Studio remains the sole refresh-token owner. `CLAUDE_LB_PREFER_REMOTE=1` remains available when owner-first routing is preferred.

## Operations and example

Use `make benchmark-startup` to discover the benchmark entry point. Command mode can measure completion or a stable useful-output marker; service mode measures `/health/startup` and `/health/ready` separately. Add `--warm-database` when comparing already-current restart behavior rather than first-use migration cost. The default history is `~/.agent-lb/startup-benchmarks.jsonl`.

For example, compare a new record with a selected prior JSONL record and a p95 ceiling. A run exceeding the ceiling exits nonzero, so the same command can be used as a regression gate.

## Failure modes and monitoring

- A live-but-not-ready service is a failed readiness sample, not a success.
- Failed command samples and exit status remain in the record instead of disappearing from aggregates.
- Prometheus startup phase labels are a fixed vocabulary; request, account, prompt, filesystem, and error-text labels are forbidden.
- Installer success requires `/health/ready`; its diagnostic points to the service log when bounded polling expires.

See [the normative startup-performance specification](spec.md), [proxy runtime observability](../proxy-runtime-observability/spec.md), and [deployment installation](../deployment-installation/spec.md).
