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
- [x] wide-scan-guard.sh as d402b454 and bb83dc35 left it (sha256 557b21db, 7a518f44) is pinned again, with a prefilter that reads every word as a possible tool, path and `cd` target and runs the guard on anything it could fail to parse; a differential test against the real guard proves it a superset.

## 7. Fix round (2026-10-08, hook-dispatcher-4: review of 62dd1f15/cfac29fd)

- [x] A floor guard's `|| true` or fallback wrapper is stripped whatever trails it (`;`, whitespace, `&`).
- [x] wide-scan-guard.sh refuses when jq or its scanner fails; the dispatcher runs a pinned guard whose needed tool is missing.
- [x] agent-lb-bootout-guard.sh is adopted from the live copy, fails closed on unreadable input, and is a floor guard.
- [x] Guard pins for adopted sources are derived at install (registry `script_sha`), so editing a source never unpins it.
- [x] The process-watch test takes the fixture's bounded observation retry.
- [x] factory `checks/harden/hook-dispatcher` passes only with receipts from test-owned Claude Code sessions on both paths.

## 8. Fix round (2026-10-08, hook-dispatcher-4 review of a7606396)

- [x] A shell comment after a floor guard's wrapper is dropped before the wrapper is read; a floor guard whose command keeps any other control operator (`;`, `&`, `|`, `||`, `&&`, a newline, a leading `!`) is refused on PreToolUse without running.
- [x] agent-lb-bootout-guard.sh tells a failed matcher (grep missing or erroring) from no match and refuses; jq reads its input directly.
- [x] railway-vars-guard.sh again refuses `railway variables --kv` and `railway run printenv|env`, custody token or not, with the earlier guard's messages.
- [x] factory `checks/harden/hook-dispatcher` probes each of these fail-open paths on a copy of the installed dispatcher.

## 9. Fix round (2026-10-08, hook-dispatcher-5: build lead decision S44 and review M1-M4 of b98c2c5d)

- [x] S44 (a): a floor guard runs only as a direct exec of its own script (`[python3|bash] <script> [args]`); any other shape is refused at install with nothing written and denied at run time. The old wrappers and the inline leaf map by exact hash to their direct execs; install-policy rewrites them in settings.
- [x] S44 (b): the inline dangerous-command leaf is `hooks/dangerous-command-guard.sh`, installed and registered like the other guards (pinned prefilter `pf_dangerous`). link-cli-guard.sh and workflow-relay-guard.py are adopted from their hand-installed copies.
- [x] S44 (c): a floor guard allows only with its receipt `floor-ok <name>` and exit 0; every floor guard reads its input with checked status and refuses on a read, jq, grep or scanner failure (seat and workflow seat guards included). factory rm-dynamic-deny and stash-guard print the receipt (factory 7ff86e29c).
- [x] M1: `bash -c 'python3 .../seat-guard.py || true'` is refused at install and denied at run time.
- [x] M2: wide-scan-guard.sh refuses when `cat` fails or reads nothing; cat is in its needed tools.
- [x] M3: the dangerous-command guard refuses when jq or grep fails, on the script and on the legacy inline bytes.
- [x] M4: folded entries naming different revs are refused with nothing written.
