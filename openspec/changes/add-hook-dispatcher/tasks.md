## 1. Specification

- [x] Define parity, fail-closed, install and process-count requirements.

## 2. Implementation

- [x] `config/coding-agents/hooks/hook-dispatch.py` (in-process, filtered, rewriter and external modes; Claude Code merge rules).
- [x] `config/coding-agents/hooks/hook-dispatch-parity.py` (sandboxed per-hook vs dispatcher comparison, process count, crash and timeout cases).
- [x] `install-policy.py --hook-dispatcher on|off` with the parity gate, sticky refold and verbatim unfold.

## 3. Verification

- [x] `tests/unit/test_hook_dispatcher.py` (fold, sticky, changed guard, verbatim rollback, parity failure, uninstall, fail closed).
- [x] Ruff on the changed files.
- [x] Parity fixture against the live Studio guard set before `--hook-dispatcher on`; factory `checks/harden/hook-dispatcher` on the installed copy.
