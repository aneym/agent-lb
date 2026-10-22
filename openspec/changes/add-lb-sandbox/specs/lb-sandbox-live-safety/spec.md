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
- **THEN** each account is reported by id, vendor, status, expiry and `auth_hash12`, never by token, and `request_log_store` names the sandbox's own SQLite store (`<root>/data/store.db`), which holds its request logs

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

### Requirement: Scans hold the store key and see every run process

A file scan with a sandbox root MUST hold its store key and report as a hit any ciphertext the key opens, whole or as a fragment, so a store copy taken before a mirror cycle re-encrypted the rows is still a leak. The log export MUST scan every byte it copies and remove a copy that holds a secret. `scan --processes` MUST be incomplete when any process naming the run (argv) does not show this run's `LB_SANDBOX_ROOT` in the environment part of `ps -E`; lb-sandbox commands of a run carry that marker in their exec environment.

#### Scenario: Snapshot before a mirror cycle
- **WHEN** `store.db` was copied into `--out` and the mirror then re-encrypted every row
- **THEN** `scan` of `--out` reports the copy

### Requirement: The live check fails on any doubt

`scripts/lb-sandbox-check` authorizes agent-lb restarts. It MUST keep the held stream open until its cutover process scan has read every process (the hold is bounded by lb-sandbox's ceiling, and the scan by the time left on it). It MUST fail when the primary had zero (or an unknown number of) requests in flight at cutover, when the held stream ended by its timeout, when any file or process scan found a secret or was incomplete, when the sandbox root was not scanned before teardown, when teardown's scan found anything, or when the store key was not unlinked.

#### Scenario: Stream finished before cutover
- **WHEN** the lb-restart log shows `had 0 in flight` at cutover
- **THEN** the restart step fails and the run result is `fail`

### Requirement: launchd loads only the jobs lb-sandbox made, and lb-restart kickstarts only that job

`start` MUST hand launchd each sandbox job by value, as the dictionary it made itself (ServiceManagement `SMJobSubmit` in this user's domain), never as a path: launchd opens a bootstrap path itself and follows links, so any path could be swapped for a link to the live plist between the write and that open. It MUST refuse a label that is already loaded, MUST confirm the job is in `gui/<uid>` (else remove it and fail), and MUST NOT load a plist under the root, which a running aux or front could have rewritten. Before a kickstart, `lb-restart --sandbox` MUST compare the loaded job (`launchctl print`) with its checked plist whole: program, every argument, the environment block (less the keys launchd adds), the working directory and every stream path, and MUST refuse a `DYLD_*` variable inherited from the domain or a print it cannot parse unambiguously. Every interpreter lb-sandbox or lb-restart starts for a sandbox (the command re-exec, `restart`'s lb-restart, the reaper) MUST run with `-I` and without `PYTHON*` or `DYLD_*` variables.

#### Scenario: Aux rewrites the primary plist before the primary loads
- **WHEN** the aux job, started first, rewrites `<root>/launchd/<label>.plist` to another program
- **THEN** launchd still loads the primary job start made, from a file outside every root

#### Scenario: Job path swapped for the live plist
- **WHEN** any path start could hand launchd is replaced by a symlink to the live plist before launchd reads it
- **THEN** launchd receives only the jobs start made (no path is handed at all) and the live plist is never loaded

#### Scenario: Live directory at the old staging name
- **WHEN** a live directory sits at `<sandboxes>/.launchd`
- **THEN** it keeps its name, inode, bytes and mode

#### Scenario: Hostile job loaded, plist restored
- **WHEN** the sandbox label was loaded with the expected argv plus `DYLD_INSERT_LIBRARIES` or a stderr path into live state, and the plist on disk was then restored
- **THEN** `lb-restart --sandbox` exits 2 before any kickstart

### Requirement: No code path prints a live launchd job

`lb-sandbox`, `lb-restart --sandbox` and `lb-sandbox-check` MUST read the pid and loaded state of a live agent-lb job only from the `launchctl list` table (pid, status, label), never `launchctl print` of a live label, whose output can carry the job's environment. Each script MUST refuse, before exec, a `launchctl print` of any label that is not the sandbox's own (`com.agent-lb.drill.sbx-` prefix).

#### Scenario: Live identity during start from live
- **WHEN** `start --from-live` or the check records the live service's pids
- **THEN** only `launchctl list` runs against the live labels

### Requirement: Scans find a token in line-wrapped base64

Every file scan and the log export MUST report a secret whose base64 form is wrapped onto lines (MIME 76-column, CRLF, indented continuation), at any byte offset and across read boundaries. The keyed ciphertext search MUST see the same unwrapped bytes: a store snapshot base64-encoded and wrapped onto indented CRLF lines is a hit, and the export gate copies none of it past the window it keeps across reads.

#### Scenario: MIME-wrapped token in exported logs
- **WHEN** a log holds the base64 of a token wrapped at 76 columns
- **THEN** `scan` reports it and the export removes the copy

#### Scenario: Indented, CRLF-wrapped stale snapshot
- **WHEN** a file holds a store snapshot from before a mirror cycle, base64-encoded at 64 columns with CRLF and two-space indentation
- **THEN** `scan` with the store key reports it, and `scan_stream` stops before the whole file is written
