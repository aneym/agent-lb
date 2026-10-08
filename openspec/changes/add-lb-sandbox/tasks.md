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

## 8. S2-3 fix round (live-safety review FAIL on cf915e10 + 84a63c41)

- [x] Both sandbox jobs and the `_boot` exec run `<venv python> -I <installed lb-sandbox>`: no script under the root runs before confinement (the root's `bin/lb-sandbox` copy is gone), and `-I` keeps the plist's `PYTHONPATH` (`<root>/runtime`, where a `sitecustomize.py` could be planted), user site and script dir out of interpreter start. `lb-restart --sandbox` pins that argv in the plist, the standby exec and the loaded launchd job, and requires the bootstrap to be its own sibling `lb-sandbox`: outside the sandboxes dir, no link, single link, owned by this user or root, no group or other write.
- [x] The sandbox plist environment holds only the keys lb-sandbox writes (`HOME`, `PATH`, `PYTHONPATH` pinned to their values, `LB_SANDBOX_ROOT`, `AGENT_LB_*`, `FORWARDED_ALLOW_IPS`, `UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN`); `DYLD_*`, `PYTHONSTARTUP` and any other key are refused.
- [x] The interpreter is checked for identity, not shape: the venv's `pyvenv.cfg` (single link, private) names its home, and the venv python must lead to `python`, `python3` or `python3.N` in that resolved dir, a native executable (Mach-O magic), owned by this user or root with no group or other write. A private shell script or a native file elsewhere is refused.
- [x] P1 (confined `_boot`/`_aux` read their own root as `{}` under the real `~/.agent-lb/sandboxes` layout) is fixed by S1-3's pinned sandboxes dir (479849b4); this round adds the confined regressions on a fake home and on a test-owned root in the real sandboxes dir (both fail on 84a63c41).
- [x] (Was deferred; done in section 9.) The aux job was bootstrapped before the primary plist was loaded by path, so a compromised aux could rewrite that plist first.

## 9. lbsb-4 fix round (S2-3 review findings parked in pending-lbsb-4, agent-lb-3 edge findings)

- [x] `start` bootstraps both jobs from bytes it made, through a private file in `<sandboxes>/.launchd` (unlinked once loaded), never from a plist under the root.
- [x] `lb-restart --sandbox` checks the loaded job whole before a kickstart: program, every argument, environment block (less `XPC_SERVICE_NAME`, `OSLogRateLimit`, `XPC_FLAGS`), working directory and stream paths against the checked plist; no inherited `DYLD_*`; a print with an entry outside its block is refused, and a plist with a control character is refused before use.
- [x] `main`'s re-exec, `restart`'s lb-restart and the sandbox reaper run `-I` with no `PYTHON*` or `DYLD_*` variable.
- [x] The edge reads a request's stream flag through `Content-Encoding: gzip` and treats Codex `/codex/responses` as streamed, so `cut_after_bytes` applies to Codex streams and `hold_stream` to a real (gzipped) Claude Code turn; the fault file is told apart by inode, not only mtime.
- [x] `status` reports `request_log_store`.
- [x] Each finding has a test that fails on dd8f4c29 for the reason it names.

## 10. lbsb-5 fix round (lbsb-4 live-service-safety review FAIL on 8c4099b6)

- [x] `bootstrap_job` never unlinks a name it did not make: each job gets a fresh random directory under `<sandboxes>/.launchd` (0500 while launchd reads) and an `O_EXCL` file; cleanup empties the file through its descriptor, unlinks the name only while it is still that single-link inode, and rmdir's only that directory. macOS has no unlink by descriptor, so check-then-unlink inside a mode-locked private directory is the narrowest delete.
- [x] `start` takes the root identity through a descriptor checked to be the directory its mkdir made (owner, volume, empty, link count, birth time) before any chmod or record; a swapped directory is refused and nothing is owned or torn down.
- [x] A failed start's teardown only rmdir's a never-stamped root, and `unlink_key` requires the root to be its own volume or the recorded directory.
- [x] Fake-live regressions for each: a live file planted at the old plist name or renamed over the job file, and live state moved into the root's name before the record, after it, or into the root itself; each fails on 8c4099b6 for the reason it names.

## 11. sys-lb-sandbox-5 fix round (build lead amendment, 2026-10-07 19:10 ET)

- [x] Credential custody floor: `launchd_pid`, `label_loaded` and `lb-sandbox-check`'s live identity read the `launchctl list` table, never `launchctl print` of a live label; `lb-sandbox` and `lb-restart` refuse a print of any label outside the sandbox's own before exec. Regressions in all three scripts' tests fail on the pre-fix code.
- [x] Structural: every created path lives in a private 0700 directory with an unpredictable name; identities (dev, ino, birth) are recorded in `ids.jsonl`; chmod and stat by descriptor; deletion only through recorded-identity removal; the root is published by exclusive rename; a failed start deletes only what it recorded. Closes M1 (live file renamed over the job file), M2 (live key moved into an unmounted root), M3 (staging dir swapped with a live dir then chmoded) and M4 (live empty dir accepted as the new root) as a class; each has a fake-live regression that fails on 26d90565. The accepted residual (no unlink by descriptor on macOS) is documented in `lb-sandbox` and the spec.
- [x] P1: scans and the export find the synthetic token in MIME and CRLF-indented wrapped base64 at shifts 0-2 and across read boundaries.
- [x] `start --from-ref <ref>`: runtime from a clean `git archive` tree of the ref, accounts from live; the check takes `--from-ref`.
- [ ] Deferred (unchanged): an agent-lb store read guard or service identity, live and sandbox alike.

## 12. sys-lb-sandbox-5 fix round 6 (codex-verifier FAIL on ef7ceb98)

- [x] M1: `lb-restart --sandbox` reads its config only as `<sandboxes>/<run>/lb-restart.json`, by name inside the pinned root with `O_NOFOLLOW`, a single-link file of this user on the root's volume, before a byte is read; the root descriptor it read through is the one the run holds.
- [x] M2: `start` hands launchd each job by value (`SMJobSubmit`), never a path launchd would open and follow; it refuses an already loaded label and confirms the job is in `gui/<uid>`. No job file exists any more.
- [x] M3: the staged root has a random name recorded before its mkdir; its birth bound is the clock read right before that mkdir; nothing start makes is chmod'ed (any mode but 0700 is refused); the attached volume's top is chmod'ed only once its descriptor is proven to be that volume.
- [x] M4: `lb-restart --sandbox` refuses a root that is not its own volume before opening anything in it.
- [x] P1: the keyed ciphertext search runs on the unwrapped bytes too, and the stream window covers a wrapped keyed fragment; an indented CRLF 64-column stale snapshot regression.
- [x] P2: the check arms the hold at lb-sandbox's ceiling and releases it only after the cutover process scan, which is bounded by the time left on the hold.
- [x] `lb-restart --sandbox` accepts, once and only at the top level, the bare `submitted job. ignore execute allowed` line launchd prints for a job submitted by value (found by the live check, run-14358: every sandbox restart exited 2).
- [x] Each regression fails on ef7ceb98 for the reason it names.
