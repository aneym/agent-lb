## Context

The startup surface has three distinct critical paths: the API process must become live and then ready, the macOS installer must replace a LaunchAgent and observe readiness, and the Claude launchers must select a compatible endpoint and start their loopback intercepting proxy before `claude` can produce useful output. Today these paths use a mix of fixed sleeps, redundant probes, and unstructured log timing. Full SQLAlchemy/Alembic schema comparison also runs after every successful startup migration even when the database revision is already current; live evidence shows that comparison can dominate readiness time.

The implementation must retain fail-closed migration/version and launcher compatibility checks, avoid secret-bearing telemetry, work without Prometheus installed, and preserve existing local/remote failover semantics. Timing comparisons must distinguish cold service startup from an already-running launcher path.

## Goals / Non-Goals

**Goals:**

- Measure process spawn-to-startup, spawn-to-ready, installer restart, launcher preparation, proxy readiness, and command useful-output timing separately.
- Produce stable JSON benchmark records that can be appended and compared over time.
- Emit one structured service-startup summary plus bounded phase histograms and total-ready metrics.
- Remove work that is provably redundant from readiness-critical paths without weakening explicit integrity and compatibility checks.
- Make the local installer readiness-driven and make launcher optimization portable with a safe fallback.

**Non-Goals:**

- Claiming comparable Docker or Helm performance without running those environments.
- Persisting high-cardinality per-process startup data in the application database.
- Removing SQLite integrity checks, migration-head checks, explicit `agent-lb-db check`, or ccgpt's native compatibility probe.
- Changing proxy request routing or account-selection policy.

## Decisions

### Use one monotonic startup recorder with stable phase names

The CLI records a monotonic process-start marker before importing Uvicorn. The lifespan creates a recorder using that marker (or an import-time fallback), wraps stable phases, and completes it immediately after startup state becomes true. It emits one `agent_lb_startup_summary` structured log containing total duration and a phase-duration map. Prometheus receives a phase histogram labeled only by a fixed phase name and outcome, plus a total startup-ready histogram/gauge. Wall-clock timestamps are metadata only; duration math always uses a monotonic clock.

This is preferred over log-line timestamp subtraction because async work and buffered logs make subtraction unreliable, and over account/request labels because those create unbounded cardinality.

### Keep historical comparisons in a benchmark JSONL artifact

`scripts/benchmark-startup.py` supports a generic command mode and an agent-lb service mode. Service mode polls `/health/startup` and `/health/ready` independently; command mode measures process completion or a caller-selected useful-output marker. Each run writes a versioned JSON record with a stable label, sanitized command executable, platform/Python/git metadata, samples, median, p95, and failures. A comparison option reports percentage changes and exits nonzero when the configured p95 regression ceiling is exceeded.

The default history lives under the resolved agent-lb data directory and can be overridden. This provides durable evidence without adding startup-time database writes or storing raw prompts/arguments.

### Make full drift comparison explicit instead of unconditional

Migration revision inspection and upgrade remain mandatory according to existing settings. The expensive metadata-to-database drift comparison becomes an explicit startup setting that defaults off; `agent-lb-db check`, migration CI, and an opt-in startup mode retain full drift validation. This preserves the deployment safety boundary (the DB must be at Alembic head) while moving deep validation out of the hot path.

The alternative—caching drift results by revision—was rejected because deployment files and model metadata may change without changing a local cache key, making invalidation subtle.

### Optimize launchers by collapsing redundant proof steps

For interactive Claude routing, `/health/ready` selects a usable endpoint and sticky account selection is deferred to the first proxied message. The proxy already carries the session id and the message route performs the same account selection, so synchronously claiming a route and then loading the full account-summary banner only delays the UI. Headless runs retain eager session-route claiming because they need deterministic reset waiting and early failure; the claim itself replaces a preceding health request. For ccgpt, native token counting proves reachability and protocol compatibility, while the account summary proves the endpoint can actually route Codex traffic; both are required because an empty local instance can implement token counting yet return 503 for every message. Failures fall through candidates and remain fail closed. `CLAUDE_LB_PREFER_REMOTE=1` supports an owner-first laptop posture while retaining a federated local mirror as fallback.

The intercepting proxy retains its portable detached subprocess because direct measurement showed that a POSIX fork increased both median and tail latency on macOS. The parent still requires the ready file plus a successful loopback connect before launching Claude. This evidence-driven choice avoids turning process-mechanism novelty into a regression.

### Replace installer sleeps with state-driven retries

After bootout, the installer polls localhost port release at subsecond cadence and attempts bootstrap as soon as launchd accepts it. Bootstrap retries use short bounded backoff instead of a mandatory five-second pre-delay. Success requires `/health/ready`, not merely the always-200 liveness endpoint, and the installer prints bootout, bootstrap, startup, and ready elapsed milliseconds.

## Risks / Trade-offs

- **Latent schema drift is no longer checked on every default boot** → Alembic-head validation remains fail closed; full drift stays in CI, `agent-lb-db check`, and an explicit startup setting.
- **Interactive selection defers account-limit diagnostics until first use** → Preserve endpoint diagnostics and fallback at launch; retain eager account/reset diagnostics for headless automation where delayed failure would be ambiguous.
- **Readiness can legitimately wait for bridge registration** → Keep startup and ready clocks separate so a healthy process is not mistaken for a ready replica.
- **Benchmarks vary with caches and machine load** → Record individual samples and machine metadata, require multiple samples for p95, and report rather than hide failures.
- **Metrics can duplicate in multiprocess deployments** → Use existing multiprocess-aware Prometheus registry conventions and low-cardinality labels only.

## Migration Plan

1. Land telemetry and tests while preserving existing behavior.
2. Enable the new default drift posture, launcher probe collapse, and readiness-driven installer.
3. Run repeatable local before/after service and launcher benchmarks, then exercise the live launchd endpoint.
4. Push only after strict OpenSpec, lint, focused tests, live restart, and client dry-run/real-run gates pass; fast-forward the MacBook checkout afterward.

Rollback is a normal git revert. Operators can immediately restore the prior full startup drift behavior through the explicit environment setting, and launcher proxy startup retains a subprocess fallback.

## Open Questions

- Docker and Helm cold-start baselines require those runtimes and will be recorded separately rather than inferred from local launchd results.
