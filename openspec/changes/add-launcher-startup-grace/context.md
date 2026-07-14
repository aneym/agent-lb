# Context: launcher startup grace and untracked startup time

## Measurement

On 2026-07-14, a reboot cold start took 62.636094 seconds from the process-start
timestamp to startup completion. Named phases accounted for about 1.34 seconds:
database took 1.26 seconds, bootstrap token 0.036 seconds, bridge schema 0.018
seconds, HTTP client 0.017 seconds, schedulers 0.002 seconds, settings and caches
0.0035 seconds, and the cache poller approximately zero. Roughly 61 seconds of
cold Python and virtual-environment imports plus boot-storm CPU contention was
therefore present in the total but absent from the phase map. Other measured
cold starts took 34 and 53 seconds, while warm restarts took 0.86 to 1.4
seconds.

At the same time, interactive launcher readiness used one local probe with a
350 ms timeout and no retry gap. A connection refusal during that probe caused
immediate fallback even when `com.aneyman.agent-lb` was loaded and still
starting.

## Decisions and constraints

- The first local readiness probe is unchanged. Launchd inspection and waiting
  occur only after a connection-level unreachable failure.
- A successful `launchctl print gui/<uid>/<label>` is evidence that the local
  service is expected to start; it is not evidence that the HTTP server is
  ready.
- The launchd label is configurable with `CLAUDE_LB_LAUNCHD_LABEL`, and the
  bounded wait is configurable with `CLAUDE_LB_STARTUP_GRACE_SECONDS`.
- Remote candidates never receive local launchd startup grace.
- HTTP responses and read timeouts do not enter the connection-unreachable
  grace path.
- `untracked_seconds` remains outside the phases JSON so existing phase-map
  parsers retain their current contract.

## Example

With the default 90-second grace, a refused local readiness connection and a
loaded launchd job prints one waiting line, polls `/health/ready` once per
second, and continues through the local endpoint when it becomes ready. Setting
`CLAUDE_LB_STARTUP_GRACE_SECONDS=0` preserves immediate fallback behavior.
