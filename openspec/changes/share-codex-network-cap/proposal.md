# Change: share the Codex network cap

## Why

The Codex launcher must prevent concurrent CLI sessions from multiplying the
configured bandwidth ceiling. A process-local proxy cannot enforce a
machine-wide aggregate and a CONNECT-only proxy breaks ordinary HTTP clients.

## What changes

- Reuse one authenticated, loopback-only proxy daemon for all opted-in Codex
  launcher processes owned by the local user.
- Shape combined upload and download bytes through a fair, bounded-burst
  scheduler.
- Support blind HTTPS CONNECT tunnels and ordinary HTTP forward-proxy requests.
- Keep loopback destinations outside the proxy so Agent LB inference traffic is
  not shaped.

## Scope

Only future processes launched through `codex-lb-launch` are covered. Existing
CLI processes, Codex Desktop, clients that ignore proxy variables, and Agent LB
loopback inference are not covered.
