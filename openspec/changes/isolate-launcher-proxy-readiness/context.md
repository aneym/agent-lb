# Context

## Scope and decisions
Concurrent headless reviewers in the same cwd timed out waiting for shim startup. Stable session IDs are valuable for account/prompt-cache affinity, not ownership of ephemeral proxy ports. `start_lb_proxy` currently unlinks the session-owned receipt; `run_lb_proxy` writes and removes that same path. Use the existing parent PID ownership pattern from activity files for readiness too. Desktop proxy remains `desktop.proxy`.

## Failure example
Launcher A and B share session S. B writes S.proxy; A exits and deletes S.proxy. B loses its receipt. Their shared S.proxy.tmp can also race during publication.

## Test authoring gate
One primary launcher test exercises real start/run entrypoints with two overlapping proxy lifecycles and independently controlled termination. Baseline loses B's ready receipt when A stops. Existing tests cover subprocess command and shared desktop configuration, not concurrent parent cleanup. Fake server/TLS avoid unrelated certificate/network behavior; actual ready-file publication and cleanup stay production-owned. No production test seam.
