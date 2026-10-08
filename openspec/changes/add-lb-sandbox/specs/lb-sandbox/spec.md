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

`lb-sandbox fault` MUST arm one fault per edge: `account_429` fails the first request after arming with a provider-shaped 429 and keeps failing that Authorization hash until cleared; `cut_after_bytes:N` closes the next streamed 200 response after N body bytes; a request streams when its body (read through `Content-Encoding: gzip`, which agent-lb uses for large bodies) holds `stream: true` or its path always streams (Codex `/codex/responses`), never judged by the upstream content-type; `hold_stream:S` (anthropic edge, S 1-120) answers the next streamed `POST /v1/messages` itself, with no provider call, sends the opening events, holds the response open until the fault is cleared or S seconds pass, then finishes the message and logs `released_by` (`clear` or `timeout`). The cooldown a fault causes MUST land in the sandbox store only.

#### Scenario: Account failover
- **WHEN** `account_429` is armed on the anthropic edge and a client sends one request
- **THEN** the edge log shows a 429 on hash H1 and a 200 on a different hash, and `status` shows the H1 account in cooldown

#### Scenario: Restart under a held stream
- **WHEN** `hold_stream:30` is armed, a client opens a stream, `lb-sandbox restart` reaches its cutover and the check then clears the fault
- **THEN** the lb-restart log shows at least one request in flight on the old primary at cutover, the edge logs `released_by: clear`, and the stream completes with `message_stop`

### Requirement: Files under a sandbox root are opened without following links

Every lb-sandbox read or write under a root MUST go through a descriptor for the root, opening each path component with O_NOFOLLOW, and MUST refuse a file with more than one link: `restart` (its log), `client-env`, `fault`, `_aux` and `start` writes, every metadata read (`sandbox.json`, `aux.json`, `front.json`, fault state, the standby pidfile, the primary log), the store key read and unlink, and the log export source. `_serve` MUST refuse a store, journal or key with more than one link before it reads a credential. The scanner MUST walk by directory descriptors, opening each entry relative to its parent with O_NOFOLLOW and reading a file only when the descriptor is the inode the listing named; under the root it MUST NOT read a file with a second link and MUST report the scan incomplete. `lb-restart --sandbox` MUST write the front preference as a new file renamed into place and remove the standby pidfile and watchdog pause through a descriptor for the real state directory, so a state directory swapped for a link is refused. `_serve` and `_aux` MUST open their own logs from a root descriptor; the sandbox plists name no `StandardOutPath`, which launchd would follow.

#### Scenario: Data dir swapped for the live data dir
- **WHEN** `<root>/data` is a link to the live data dir at stop
- **THEN** the live key is not unlinked, `custody.key_unlinked` is false and stop exits 1

#### Scenario: Metadata swapped for a link to live state
- **WHEN** `<root>/sandbox.json` is a link (or a hard link) to the live `state/front.json` at stop
- **THEN** stop reads it as no metadata, signals and deletes nothing, and exits 2

#### Scenario: State directory swapped during a sandbox restart
- **WHEN** `<root>/state` is replaced by a link to the live state directory after the restart guard ran
- **THEN** setting the preference and removing the standby pidfile are refused and the live `front-preferred-port` and `lb-standby.pid` keep their bytes

#### Scenario: Directory swapped for a link mid-walk
- **WHEN** a directory the scanner has listed but not entered is replaced by a link to another tree
- **THEN** the scanner does not follow it and reports the scan incomplete

#### Scenario: Log directory swapped after the scan
- **WHEN** `logs/d` is replaced by a link to `<root>/data` after the teardown scan read it
- **THEN** the export refuses `logs/d/encryption.key` and the store key never reaches `--logs-to`

### Requirement: Every process that works in a root runs under kernel confinement

The app, the front and the aux open paths by name with code lb-sandbox does not own. Each sandbox root MUST therefore be its own volume: `start` mounts a fresh encrypted sparse image (random passphrase held only in the `start` process, given to hdiutil on stdin) at the root, so no name under the root can be a hard link to a live file and the image on disk is ciphertext. `_serve`, `_boot` and `_aux` MUST refuse a root that is not its own volume. `_boot` and `_aux` MUST put themselves (and so the front and every other child) under a Seatbelt profile that denies writes anywhere under the home directory except the root and denies reads of the agent-lb home, the legacy store directory and the live plist (the installed runtime's code stays readable), before they open anything under the root. `start` MUST populate the root (runtime copy, bootstrap copy, plists, metadata) in a child under the write-confinement profile, and the store reads of `status`, `scan` and `stop` MUST run in a child under the full profile. `start` MUST take the root's (device, inode) through a descriptor that is the directory its mkdir just made (this user's, on the sandboxes dir's volume, empty, born no earlier than the mkdir), and MUST refuse, owning nothing, a directory swapped into the name in between. A failed start's teardown MUST only rmdir a root that was never stamped, and MUST unlink the store key only from a root that is its own volume or the recorded directory. `status` MUST report `own_volume` and whether the primary, aux and front are confined; the live check fails unless all are true. Stop MUST detach the volume and delete its image.

#### Scenario: Front temp file linked to live state
- **WHEN** `<root>/state/front.json.tmp` is a link to the live `state/front.json` while the front runs
- **THEN** the front's write is denied by the profile and the live file keeps its bytes

#### Scenario: Data dir swapped after the custody checks
- **WHEN** `<root>/data` is replaced by a link to the live agent-lb home after `_serve` validated it
- **THEN** the app's SQLite and key opens, and `read_accounts`, are denied; nothing under the live home is read or written

#### Scenario: Live state moved into a new root's name
- **WHEN** right after start's mkdir the new root is renamed aside and live `state` moved into its name, and a non-image file at the run's image name makes the volume attach fail
- **THEN** start refuses before recording or chmod'ing the directory, and nothing in it is deleted; live state moved in after the record, or live files moved into the root start made, are left in place by the failed start's teardown

#### Scenario: Hard link to a live file
- **WHEN** a caller tries to hard-link a live file into a root
- **THEN** the link fails across volumes (EXDEV)

### Requirement: Stop signals only processes of a stamped sandbox

`stop` and `gc` MUST NOT signal, scan or delete anything unless `<root>/sandbox.json` is a single-link regular file whose `run_id` is the run id given (and whose label, when present, is the run's label); otherwise `stop` exits 2. Processes MUST be matched for signalling by the sandbox root path (or a path inside it) or the run's launchd label as a whole token, never by the run id as a bare word.

#### Scenario: A run id that is a word of the live command line
- **WHEN** `lb-sandbox stop --run-id aneyman` (or `runtime`, `agent-lb`) runs with no such sandbox
- **THEN** no process is signalled; the live agent-lb, whose command line holds those words as path segments, is untouched

### Requirement: Scans hold the store key and see every run process

A file scan with a sandbox root MUST hold its store key and report as a hit any ciphertext the key opens, whole or as a fragment, so a store copy taken before a mirror cycle re-encrypted the rows is still a leak. The log export MUST scan every byte it copies and remove a copy that holds a secret. `scan --processes` MUST be incomplete when any process naming the run (argv) does not show this run's `LB_SANDBOX_ROOT` in the environment part of `ps -E`; lb-sandbox commands of a run carry that marker in their exec environment.

#### Scenario: Snapshot before a mirror cycle
- **WHEN** `store.db` was copied into `--out` and the mirror then re-encrypted every row
- **THEN** `scan` of `--out` reports the copy

### Requirement: Stop tears down to nothing and proves it

`lb-sandbox stop` MUST boot out both labels, stop a leftover standby by its pidfile, stop every process whose command line names the sandbox root or the run's launchd label, report a leftover process (one naming the root, the label or `--run-id R`) by pid and command hash only, scan the whole root (store and key at their own paths excepted) before deleting it, copy the logs to `--logs-to` only after that scan is complete and clean, unlink the store key, detach the root's volume, delete the root and the volume image, and report `clean` only when no label is loaded, no listener holds a run port, no process names the run, the volume is detached, the image is gone and the root is gone. It MUST exit 1 when the scan found anything, could not read everything or could not load every secret, even when cleanup succeeded.

#### Scenario: Clean stop
- **WHEN** a started sandbox is stopped
- **THEN** `clean` is true, `labels_loaded`, `listeners` and `processes` are empty, `logs.scan_scope` is `root` and `custody.key_unlinked` is true

#### Scenario: Token outside the logs
- **WHEN** a mirrored token sits in `<root>/state/` at stop
- **THEN** the teardown scan reports it, no log is exported and stop exits 1

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

### Requirement: lb-sandbox creates, chmods and deletes only inodes it recorded, inside a private directory

Every path `start` creates MUST live under a private directory it makes with an unpredictable name (`.lbsbx.<run hash>.<32 random hex>`, 0700) inside the pinned sandboxes dir: the record (`ids.jsonl`, `O_EXCL`), the volume image and the staged mountpoint (a random stage name, recorded before its mkdir), which is published to `<sandboxes>/<run-id>` with an exclusive rename (`renameatx_np RENAME_EXCL` on macOS, `renameat2 RENAME_NOREPLACE` elsewhere). Each object's identity (device, inode, birth time) MUST be recorded when it is made, and stat MUST go through its own descriptor (`O_NOFOLLOW`, `O_DIRECTORY` for directories). A directory start makes MUST NOT be chmod'ed: it MUST be refused unless it is mode 0700 as its mkdir left it, this user's, empty, on its parent's volume and born no earlier than the clock read just before that mkdir. Only the attached volume's top is chmod'ed, after its descriptor is proven to be that volume. Deletion MUST touch only a recorded inode: the name is renamed exclusively into the private directory, opened there, compared with its record, emptied through its descriptor (files), re-checked by name and only then unlinked or rmdir'd; anything that does not match is renamed back and left. The store key MUST be unlinked only on the run's own recorded volume, which no live file can reach by rename. A failed start MUST delete only what that start recorded. macOS has no unlink by descriptor; the remaining check-then-unlink window is accepted only inside the 0700 private directory with the inode re-verified by descriptor just before the unlink.

#### Scenario: Live directory created at the root name during start
- **WHEN** a live empty directory takes `<sandboxes>/<run-id>` before the exclusive publish
- **THEN** start exits non-zero and the live directory keeps its name, inode and mode

#### Scenario: Newer live directory moved onto the staged root
- **WHEN** an empty live 0755 directory, made after the private directory, is renamed onto the stage name between the mkdir and the open
- **THEN** start refuses it and it keeps its name, inode and mode; nothing of it is recorded, chmod'ed or removed

#### Scenario: Live key moved into an unmounted root's data dir
- **WHEN** a live `encryption.key` is renamed into `<root>/data/` and the start then fails
- **THEN** the live key keeps its inode and bytes; only the run's own recorded objects are removed

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

### Requirement: lb-restart works only in a root that is its own volume

`lb-restart --sandbox <config>` MUST take the config only as `<sandboxes>/<run>/lb-restart.json`, opened by name inside the root it pins (from `/` down, no link) with `O_NOFOLLOW`, and MUST refuse it unless it is a single-link regular file of this user on the root's volume, before reading a byte. The root MUST be a 0700 directory of this user and the top of its own volume (lb-sandbox attaches one at every root): `rename(2)` and `link(2)` never cross volumes, so no live file can be moved or linked into it. A root on the sandboxes dir's volume MUST be refused before any file in it is opened.

#### Scenario: Config swapped for a link to live state
- **WHEN** a running sandbox's `lb-restart.json` is replaced by a symlink or hard link to live `state/front.json`
- **THEN** `lb-restart --sandbox` exits 2 without reading the live file

#### Scenario: Live file renamed into the lock of a hand-made root
- **WHEN** a 0700 directory with a valid config and plist sits under the sandboxes dir on the home volume, and live `state/front.json` was renamed to its `lb-restart.lock`
- **THEN** the root is refused before the lock is opened, and the file keeps its bytes

### Requirement: A sandbox can run code from a git ref

`start --from-ref <ref> [--repo DIR]` MUST take the runtime code (`app`, `config`, `scripts`) from that commit as a clean tree (`git archive`), never from a working tree, and accounts from live as `--from-live` does. A ref that is not a plain name or does not resolve to a commit MUST be refused before anything is made. `start` output and the check's `result.json` name the runtime source.

#### Scenario: Start from a branch
- **WHEN** `start --run-id R --from-ref feature-x` runs
- **THEN** `<root>/runtime` holds that commit's tree and `runtime.ref` names its commit sha
