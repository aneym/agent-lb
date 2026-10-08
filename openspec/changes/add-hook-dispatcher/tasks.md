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

## 4. Fix round (2026-10-07 reviews of 4e1cea13)

- [x] Crash notices beside a JSON answer ride in systemMessage; the fixture compares the notices the user is shown.
- [x] The exec'd rewriter keeps its fractional timeout and leaves no payload file.
- [x] Damaged entries fall back to the next registry; payload write failures refuse and clean up; non-PreToolUse failures stay notices.
- [x] Folds switch atomically by rev; entries without a rev keep registry.legacy.json.
- [x] Process counts observed by kqueue, with a self-test, in a git repo and outside one.
- [x] no-chrome-guard prefilter covers factory f76e54a5a (PC rule).

## 5. Fix round (2026-10-08, hook-dispatcher-3)

- [x] The exec'd rewriter's own timeout is armed before the exec: a late rewrite never lands, the kill shows a notice; the fixture's 1.45 s rewriter under a 1 s timeout proves it.
- [x] `~/.claude/hooks/wide-scan-guard.sh` is adopted as `config/coding-agents/hooks/wide-scan-guard.sh` and installed by install-policy.py; edits go to the source only.

## 6. Fix round (2026-10-08 review of 234faaf1 and dbb1d5dc)

- [x] wide-scan-guard.sh is a floor guard on both the dispatcher and the fixture (M1).
- [x] A floor guard whose command shape the dispatcher does not read still runs without its trailing wrapper (M2).
- [x] `--uninstall` keeps the adopted wide-scan-guard.sh, which settings still register (M3).
- [x] The fold checks shared commands against groups left per-hook too, until stable.
- [x] The child-run rewriter is resolved on the caller's PATH, before git's directory is put first.

