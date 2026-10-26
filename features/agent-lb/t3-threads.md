---
id: t3-threads
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/t3/threads/sync", "route:/api/t3/threads/burn"]
paths: ["app/modules/t3_threads/", "features/agent-lb/t3-threads.md", "docs/t3-thread-burn.md"]
proofs: []
---
# T3 Threads

## Sub-features
T3 threads sync registry, burn attribution per thread and the `agent-lb threads` report. The spec lives in `docs/t3-thread-burn.md`.

## How to get to it
Run `scripts/t3-thread-collector` on each T3 host and call `GET /api/t3/threads/burn` or run `agent-lb threads`.

## Driving it
Use an HTTP client with dashboard access, or the CLI.

## Gotchas
Responses carry no cost fields; usage is a share of a pool's weekly quota.
