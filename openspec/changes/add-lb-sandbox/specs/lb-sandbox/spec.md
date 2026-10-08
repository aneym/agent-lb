## ADDED Requirements

### Requirement: Sandbox credentials stay inside agent-lb processes

`lb-sandbox` MUST read the live federation token only inside its own processes (`_serve`, `scan`, `stop`). `_serve` MUST hand the token to `lb-sandbox _boot` through an inherited pipe fd, never through an exec environment: `_boot` reads it after exec, loads the app settings with it, removes it from `os.environ` and runs the app in the same process. The token MUST NOT appear in any exec environment (`ps eww`), the sandbox plist, any file, any argv or any log. Command output MUST carry ports, account ids, 12-character Authorization hashes, pids and counts, never a token.

#### Scenario: Primary boot
- **WHEN** launchd starts `lb-sandbox _serve <root>`
- **THEN** `ps eww` of the app process shows its sandbox environment and no token, the app settings hold the token, and the sandbox root holds no file containing it

#### Scenario: Process scan
- **WHEN** a caller runs `lb-sandbox scan --run-id R --processes`
- **THEN** the exec environment of every process naming the run is searched inside lb-sandbox, and the output lists the pids whose environment `ps` showed and the pids with a hit, never a value

#### Scenario: Status
- **WHEN** a caller runs `lb-sandbox status --run-id R`
- **THEN** each account is reported by id, vendor, status, expiry and `auth_hash12`, never by token

### Requirement: Sandbox custody does not widen exposure beyond the live service

The sandbox runs at the live service's UID; an OS identity boundary for both is a separate change. Within that, the sandboxes directory and every root MUST be mode 0700, the store key MUST be created 0600 with `O_EXCL` and `O_NOFOLLOW` and unlinked at stop, and no token, store or key MUST reach `--out` or `--logs-to`. Every scan MUST look for the live token, every mirrored token, the store key and every stored ciphertext; only the store and key files at their own paths in `<root>/data` are exempt, so a copy or hard link anywhere else is a hit. Log export MUST refuse a file with more than one link.

#### Scenario: Store copied into an output directory
- **WHEN** a copy of `store.db` or `encryption.key` sits in a scanned directory
- **THEN** the scan reports it as a hit

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

`lb-restart --sandbox <config>` MUST rebind every path, label and port constant to the sandbox and MUST exit 2 without side effects when the config breaks the sandbox label, port or root rules, a path resolves outside the root, or `--from`, `--files`, `--backup` or `--reload-plist` is also given. Without `--sandbox`, lb-restart MUST keep its live constants. In sandbox mode it MUST also refuse (exit 2) a lock, log, pidfile, preferred-port file or plist under the root that is a link or shares its inode with another name (a hard link), at the guard and again at every open, and a plist whose ProgramArguments are not `lb-sandbox _serve <root>` on the primary port or whose environment names a store, key, data dir or path outside the root or a URL outside the sandbox ports.

#### Scenario: Lock hard-linked to live state
- **WHEN** `<root>/lb-restart.lock` is a hard link to the live `front-preferred-port`
- **THEN** lb-restart exits 2 and the live file is unchanged

#### Scenario: Plist with the live store
- **WHEN** the in-root plist sets `AGENT_LB_DATABASE_URL` to the live store
- **THEN** lb-restart exits 2 before starting a standby

#### Scenario: Live port in a sandbox config
- **WHEN** `lb-restart --sandbox cfg.json` names `standby_port` 2459
- **THEN** it exits 2 and no lock, file or process is created

### Requirement: Faults are injected at the sandbox edge only

`lb-sandbox fault` MUST arm one fault per edge: `account_429` fails the first request after arming with a provider-shaped 429 and keeps failing that Authorization hash until cleared; `cut_after_bytes:N` closes the next streamed 200 response after N body bytes; `hold_stream:S` (anthropic edge, S 1-120) answers the next streamed `POST /v1/messages` itself, with no provider call, sends the opening events, holds the response open until the fault is cleared or S seconds pass, then finishes the message and logs `released_by` (`clear` or `timeout`). The cooldown a fault causes MUST land in the sandbox store only.

#### Scenario: Account failover
- **WHEN** `account_429` is armed on the anthropic edge and a client sends one request
- **THEN** the edge log shows a 429 on hash H1 and a 200 on a different hash, and `status` shows the H1 account in cooldown

#### Scenario: Restart under a held stream
- **WHEN** `hold_stream:30` is armed, a client opens a stream, `lb-sandbox restart` reaches its cutover and the check then clears the fault
- **THEN** the lb-restart log shows at least one request in flight on the old primary at cutover, the edge logs `released_by: clear`, and the stream completes with `message_stop`

### Requirement: Files under a sandbox root are opened without following links

Every lb-sandbox read or write under a root MUST go through a descriptor for the root, opening each path component with O_NOFOLLOW, and MUST refuse a file with more than one link: `restart` (its log), `client-env`, `fault`, `_aux` and `start` writes, the store key read and unlink, and the log export source. `_serve` MUST refuse a store, journal or key with more than one link before it reads a credential.

#### Scenario: Data dir swapped for the live data dir
- **WHEN** `<root>/data` is a link to the live data dir at stop
- **THEN** the live key is not unlinked, `custody.key_unlinked` is false and stop exits 1

#### Scenario: Log directory swapped after the scan
- **WHEN** `logs/d` is replaced by a link to `<root>/data` after the teardown scan read it
- **THEN** the export refuses `logs/d/encryption.key` and the store key never reaches `--logs-to`

### Requirement: Scans hold the store key and see every run process

A file scan with a sandbox root MUST hold its store key and report as a hit any ciphertext the key opens, whole or as a fragment, so a store copy taken before a mirror cycle re-encrypted the rows is still a leak. The log export MUST scan every byte it copies and remove a copy that holds a secret. `scan --processes` MUST be incomplete when any process naming the run (argv) does not show this run's `LB_SANDBOX_ROOT` in the environment part of `ps -E`; lb-sandbox commands of a run carry that marker in their exec environment.

#### Scenario: Snapshot before a mirror cycle
- **WHEN** `store.db` was copied into `--out` and the mirror then re-encrypted every row
- **THEN** `scan` of `--out` reports the copy

### Requirement: Stop tears down to nothing and proves it

`lb-sandbox stop` MUST boot out both labels, stop a leftover standby by its pidfile, stop every process whose command line names the sandbox root or the run id, report a leftover process by pid and command hash only, scan the whole root (store and key at their own paths excepted) before deleting it, copy the logs to `--logs-to` only after that scan is complete and clean, unlink the store key, delete the root and report `clean` only when no label is loaded, no listener holds a run port, no process names the run and the root is gone. It MUST exit 1 when the scan found anything, could not read everything or could not load every secret, even when cleanup succeeded.

#### Scenario: Clean stop
- **WHEN** a started sandbox is stopped
- **THEN** `clean` is true, `labels_loaded`, `listeners` and `processes` are empty, `logs.scan_scope` is `root` and `custody.key_unlinked` is true

#### Scenario: Token outside the logs
- **WHEN** a mirrored token sits in `<root>/state/` at stop
- **THEN** the teardown scan reports it, no log is exported and stop exits 1

### Requirement: The live check fails on any doubt

`scripts/lb-sandbox-check` authorizes agent-lb restarts. It MUST fail when the primary had zero (or an unknown number of) requests in flight at cutover, when the held stream ended by its timeout, when any file or process scan found a secret or was incomplete, when the sandbox root was not scanned before teardown, when teardown's scan found anything, or when the store key was not unlinked.

#### Scenario: Stream finished before cutover
- **WHEN** the lb-restart log shows `had 0 in flight` at cutover
- **THEN** the restart step fails and the run result is `fail`
