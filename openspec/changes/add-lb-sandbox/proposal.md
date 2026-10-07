## Why

Drills and factory checks need an agent-lb with working accounts that is not the live service. The first agent-lb hardened check had to read the live federation token itself and run its own Authorization-header proxy, which no seat may do (factory-operations defect S5). Credential custody belongs to agent-lb, so agent-lb ships the sandbox and keeps every token inside its own processes.

## What Changes

- Add `scripts/lb-sandbox` (installed at `~/.agent-lb/bin/lb-sandbox`): `start`, `status`, `client-env`, `fault`, `restart`, `scan`, `stop`, `gc`. A sandbox is a copy of the installed runtime under `~/.agent-lb/sandboxes/<run-id>/` with its own SQLite store, run by two launchd jobs labelled `com.agent-lb.drill.sbx-<run-id>` and `...-aux`.
- The primary job (`lb-sandbox _serve`) reads the live federation token from the live plist into its own environment at exec time; the sandbox plist, files, argv and logs never hold it. It mirrors the live pool through a peer gate that forwards only `GET /api/federation/mirror` and `GET /api/federation/status` and answers 403 to everything else.
- The aux job runs the peer gate, two edge proxies to api.anthropic.com and chatgpt.com that log only a 12-character hash of the Authorization value and inject armed faults (`account_429`, `cut_after_bytes:N`), and the front as its child.
- Add `lb-restart --sandbox <config.json>`: the same blue/green restart against a sandbox; every path, label and port constant is rebound and a guard refuses live labels, live ports and paths outside `~/.agent-lb/sandboxes/`. Without the flag lb-restart behaves as before.
- Add `scripts/lb-sandbox-check`, the live check: it drives the installed `lb-sandbox` end to end and proves the live service kept its pids and preferred port.

## Impact

- Capability: `lb-sandbox` (new).
- No app code change. The live service, plist, state and database are only read. Installing the two scripts to `~/.agent-lb/bin` is not a service restart.
- Money path (rule 8): `lb-restart` and account custody; reviewed with three lenses.
