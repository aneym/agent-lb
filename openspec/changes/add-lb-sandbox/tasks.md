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
