## 1. Specification

- [x] Define the sandbox commands, guards, custody and teardown requirements.

## 2. Implementation

- [x] `scripts/lb-sandbox` with `_serve` (token read at exec) and `_aux` (peer gate, edges, front).
- [x] `lb-restart --sandbox` with the rebinding guard; default constants unchanged.
- [x] `scripts/lb-sandbox-check`, the live check.
- [x] Install: `install -m 755 scripts/lb-sandbox scripts/lb-restart ~/.agent-lb/bin/` (no installer copies lb-restart today).

## 3. Verification

- [x] `tests/unit/test_lb_sandbox.py` and `tests/unit/test_lb_restart_sandbox.py` (guards, rebinding, scan finds a planted token raw and base64, `_serve` writes nothing).
- [x] Ruff on the three scripts.
- [x] `scripts/lb-sandbox-check --out <dir>` on Studio against the installed copy.

## 4. Fix round (review FAIL on c957bb05e)

- [x] Token handed to `_boot` over a pipe fd; never in an exec environment; `scan --processes` checks `ps eww` of every run process.
- [x] Custody parity: 0700 directories, 0600 `O_EXCL` key unlinked at stop, key and ciphertexts scanned for, export refuses hard links.
- [x] `hold_stream:S` edge fault; the check releases it after the cutover and fails on zero in flight or a timeout release.
- [x] Teardown scans the whole root; `stop` and the check fail on a hit or an incomplete scan.
- [x] Each review finding has a test that fails on c957bb05e.
- [ ] Deferred: agent-lb store read guard / service identity (live and sandbox).

## 5. Fix round 2 (review FAIL on 41a2f667)

- [x] Confined file access under the root: every component opened O_NOFOLLOW, single-link files only (log export source, key read and unlink, restart log, client-env, fault, aux and start writes).
- [x] `_serve` refuses a store, journal or key with more than one link.
- [x] Scans hold the store key: any ciphertext it opens, or a fragment of one, is a hit; the export scans every byte it copies.
- [x] `scan --processes` is incomplete when any run process hides its environment; run commands carry the marker.
- [x] Teardown stops processes naming the run id; leftovers reported by pid and hash only.
- [x] `lb-restart --sandbox`: hard links refused at the guard and at every open; the plist must run `_serve <root>` with an env inside the root.
- [x] Each finding has a test that fails on 41a2f667.

## 6. Fix round 3 (live-safety review FAIL on 1667b000, 41a2f667, 397b5122)

- [x] Each root is its own volume (encrypted sparse image mounted at the root; passphrase only in memory): no hard link reaches a live file. `_serve`, `_boot` and `_aux` refuse a root that is not.
- [x] `_boot` and `_aux` (and the front, their child) run under the root's Seatbelt profile: no write under home outside the root, no read of live custody. `start` populates the root, and `status`/`scan`/`stop` read the store, in confined children.
- [x] Metadata reads (`sandbox.json`, aux and front state, fault state, pidfile, primary log) go through root descriptors; the plists name no `StandardOutPath`; `_serve` and `_aux` open their logs from the root.
- [x] The scanner walks by directory descriptors; under the root it never reads a file with a second link.
- [x] `lb-restart --sandbox` renames the preference and unlinks the pidfile and pause through a state-dir descriptor.
- [x] `stop` and `gc` act only on a sandbox stamped with the run id (else exit 2) and signal only processes naming the root or the run's label, never a bare word.
- [x] `status` reports `own_volume` and `confined`; the live check fails unless the primary, aux and front are confined on their own volume.
- [ ] Deferred: same-UID force-unmount of a running sandbox's volume (covered by the service identity follow-up); `launchctl bootstrap` reads its plist by path.

## 7. S2-2 fix round 4 (live-safety review FAIL on cf915e10)

- [x] Every sandbox job runs the installed runtime's venv python (`~/.agent-lb/runtime/agent-lb/.venv/bin/python`), never `<root>/runtime/.venv/bin/python`. `lb-restart --sandbox` requires that program in the plist, checks what it leads to (no link from the top to its bin dir; a regular executable owned by this user or root, not group or other writable, outside every sandbox root) just before the standby exec, and checks the loaded launchd job's program and arguments before each kickstart. `_serve` checks the same before its exec.
- [x] The sandboxes dir and every ancestor must be real directories (no link) and the sandboxes dir this user's own with no group or other write. `check_root` no longer resolves paths; root opens walk from the top with `O_NOFOLLOW`; `stop` removes the root by name inside a pinned sandboxes-dir descriptor; `restart` writes its log only on a root that is its own volume.
- [x] `lb-restart` reads `front.json` replaced between open and fstat (st_nlink 0) as the front's own write and reads the name again; only a second hard link is refused.
- [x] Each finding has a test that fails on cf915e10.
