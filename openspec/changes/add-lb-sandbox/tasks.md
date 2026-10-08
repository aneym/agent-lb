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
