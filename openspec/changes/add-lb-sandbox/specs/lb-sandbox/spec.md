## ADDED Requirements

### Requirement: Sandbox credentials stay inside agent-lb processes

`lb-sandbox` MUST read the live federation token only inside its own processes (`_serve` at exec time, `scan`, `stop`). The token MUST NOT appear in the sandbox plist, any file, any argv or any log. Command output MUST carry ports, account ids, 12-character Authorization hashes and counts, never a token.

#### Scenario: Primary boot
- **WHEN** launchd starts `lb-sandbox _serve <root>`
- **THEN** the token is added to the exec environment only, and the sandbox root holds no file containing it

#### Scenario: Status
- **WHEN** a caller runs `lb-sandbox status --run-id R`
- **THEN** each account is reported by id, vendor, status, expiry and `auth_hash12`, never by token

### Requirement: The sandbox can read the live pool and never write to it

The sandbox MUST reach the live service only through its peer gate, which forwards `GET /api/federation/mirror` and `GET /api/federation/status` and answers 403 to every other method or path, counting each refusal. Every sandbox listener MUST bind 127.0.0.1 on a port in 2470-2599, never 1455, 2455, 2457 or 2459.

#### Scenario: Usage push from the sandbox
- **WHEN** the sandbox mirror loop posts a usage report to its peer
- **THEN** the gate answers 403 and `status.gate.refused` counts it

### Requirement: Sandbox guards refuse before anything is touched

`lb-sandbox` MUST exit 2 without side effects when a run id does not match `^[a-z0-9][a-z0-9-]{0,79}$` or its root already exists (start), a label is not a `com.agent-lb.drill.` label, a port is under 2470, is 1455, 2455, 2457 or 2459, or repeats, a root is not directly under `~/.agent-lb/sandboxes/`, or `launchctl` does not resolve to `/bin/launchctl`.

#### Scenario: launchctl shim on PATH
- **WHEN** a `launchctl` other than `/bin/launchctl` is first on PATH
- **THEN** `lb-sandbox` exits 2 and starts, stops and restarts nothing

### Requirement: lb-restart sandbox mode keeps live and sandbox apart

`lb-restart --sandbox <config>` MUST rebind every path, label and port constant to the sandbox and MUST exit 2 without side effects when the config breaks the sandbox label, port or root rules, a path resolves outside the root, or `--from`, `--files`, `--backup` or `--reload-plist` is also given. Without `--sandbox`, lb-restart MUST keep its live constants.

#### Scenario: Live port in a sandbox config
- **WHEN** `lb-restart --sandbox cfg.json` names `standby_port` 2459
- **THEN** it exits 2 and no lock, file or process is created

### Requirement: Faults are injected at the sandbox edge only

`lb-sandbox fault` MUST arm one fault per edge: `account_429` fails the first request after arming with a provider-shaped 429 and keeps failing that Authorization hash until cleared; `cut_after_bytes:N` closes the next streamed 200 response after N body bytes. The cooldown it causes MUST land in the sandbox store only.

#### Scenario: Account failover
- **WHEN** `account_429` is armed on the anthropic edge and a client sends one request
- **THEN** the edge log shows a 429 on hash H1 and a 200 on a different hash, and `status` shows the H1 account in cooldown

### Requirement: Stop tears down to nothing and proves it

`lb-sandbox stop` MUST boot out both labels, stop a leftover standby by its pidfile, stop every process whose command line names the sandbox root, copy the logs to `--logs-to` only after a clean token scan, delete the root and report `clean` only when no label is loaded, no listener holds a run port, no process names the run and the root is gone.

#### Scenario: Clean stop
- **WHEN** a started sandbox is stopped
- **THEN** `clean` is true and `labels_loaded`, `listeners` and `processes` are empty
