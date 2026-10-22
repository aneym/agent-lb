## ADDED Requirements

### Requirement: Sandbox guards refuse before anything is touched

`lb-sandbox` MUST exit 2 without side effects when a run id does not match `^[a-z0-9][a-z0-9-]{0,79}$` or its root already exists (start), a label is not a `com.agent-lb.drill.` label, a port is under 2470, is 1455, 2455, 2457 or 2459, or repeats, a root is not directly under `~/.agent-lb/sandboxes/`, or `launchctl` does not resolve to `/bin/launchctl`.

#### Scenario: launchctl shim on PATH
- **WHEN** a `launchctl` other than `/bin/launchctl` is first on PATH
- **THEN** `lb-sandbox` exits 2 and starts, stops and restarts nothing

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

### Requirement: Stop tears down to nothing and proves it

`lb-sandbox stop` MUST boot out both labels, stop a leftover standby by its pidfile, stop every process whose command line names the sandbox root or the run's launchd label, report a leftover process (one naming the root, the label or `--run-id R`) by pid and command hash only, scan the whole root (store and key at their own paths excepted) before deleting it, copy the logs to `--logs-to` only after that scan is complete and clean, unlink the store key, detach the root's volume, delete the root and the volume image, and report `clean` only when no label is loaded, no listener holds a run port, no process names the run, the volume is detached, the image is gone and the root is gone. It MUST exit 1 when the scan found anything, could not read everything or could not load every secret, even when cleanup succeeded.

#### Scenario: Clean stop
- **WHEN** a started sandbox is stopped
- **THEN** `clean` is true, `labels_loaded`, `listeners` and `processes` are empty, `logs.scan_scope` is `root` and `custody.key_unlinked` is true

#### Scenario: Token outside the logs
- **WHEN** a mirrored token sits in `<root>/state/` at stop
- **THEN** the teardown scan reports it, no log is exported and stop exits 1

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
