## ADDED Requirements

### Requirement: The dispatcher gives Claude Code the per-hook answer

The hook dispatcher MUST give Claude Code the same decision, block message, updated input, context and exit-code effect as running the same hooks per-hook, for PreToolUse, PostToolUse, UserPromptSubmit and Stop. It MUST run every hook of the entry; it MAY skip a hook only when a prefilter pinned to that hook's exact bytes proves the hook does nothing for the input, or when the hook is the side-effect-free rewriter and a block or a later rewrite decides.

#### Scenario: A guard denies a Bash call
- **WHEN** one guard of the Bash entry exits 2 with stderr S
- **THEN** Claude Code shows `[<that guard's command>]: S`, as it did per-hook

#### Scenario: Two guards deny
- **WHEN** two guards block the same call
- **THEN** the call is denied and both messages appear, in config order

#### Scenario: A guard crashes or hangs
- **WHEN** a guard raises, exits non-zero other than 2, or runs past its timeout
- **THEN** the effect is what Claude Code did per-hook: a crash is a non-blocking error, a timeout is cancelled, a guard that refuses on its own error still refuses

#### Scenario: A guard crashes beside a JSON answer
- **WHEN** one guard fails without JSON (exit 1, stderr S) and another answers with JSON
- **THEN** the merged JSON answer carries S in its systemMessage, in config order, because Claude Code (2.1.293) ignores the stderr of a hook whose stdout is JSON, so the user still sees the notice

#### Scenario: The exec'd rewriter hangs
- **WHEN** the dispatcher execs into `rtk hook claude` and it runs past its own timeout T
- **THEN** it is stopped at T (to the fraction of a second, from the exec) by a timer armed before the exec, with nothing written, so no late rewrite applies, and no payload temp file is left
- **AND** Claude Code shows the kill as a failed-hook notice (build lead decision (a), 2026-10-08), where per-hook the cancel showed nothing

#### Scenario: A floor guard runs only as a direct exec (S44, 2026-10-08)
- **WHEN** a PreToolUse floor guard (the seat, workflow seat, relay, rm, stash, secret, link-cli, dangerous-command, wide-scan, railway and agent-lb bootout guards) is registered as anything but a direct exec of its own script with plain arguments (`[python3|bash] <script> [args]`): a nested `bash -c`, an inline jq or grep leaf, `||`, `;`, `&`, a pipe or a comment
- **THEN** the installer refuses it with nothing written, and the dispatcher denies the call without running it; the exact commands earlier installs wrote (the inline dangerous-command leaf, the seat and workflow seat guards' `2>/dev/null || { printf ...; }` wrappers) map to their direct execs

#### Scenario: A floor guard allows only with its receipt
- **WHEN** a floor guard exits 0 without `floor-ok <script name>` as its last stdout line (the dispatcher asks for it with HOOK_FLOOR_RECEIPT), exits non-zero other than 2, times out, crashes or is missing
- **THEN** the call is denied; with the receipt the guard's own answer passes through without the receipt line, and per-hook (not asked) the guard prints none

#### Scenario: A floor guard's dependency fails
- **WHEN** a floor guard cannot read its input (cat, jq or the JSON reader missing or failing) or its matcher or scanner fails (grep or python3 missing or failing, a script the workflow seat guard cannot parse)
- **THEN** the guard refuses the call on both paths, and the dispatcher never skips a guard whose needed tool is missing

#### Scenario: Claude Code reads the same outcome on both paths
- **WHEN** test-owned Claude Code sessions run one Bash call each on the per-hook and the dispatcher path (an allow, a JSON deny, a guard exiting 1, a guard blocking with exit 2 and a message)
- **THEN** each session's transcript shows the same outcome on both paths (blocked or ran, the text the model reads, the notices), and the harden check is pass only with those receipts

#### Scenario: The rewriter runs beside another hook
- **WHEN** the rewriter runs as a child because another hook of the entry also runs
- **THEN** it is the `rtk` the caller's own PATH finds, not one in git's directory, which only the rewriter's children see first

### Requirement: Half-installed states fail closed

The dispatcher MUST refuse a PreToolUse call (exit 2 and a JSON deny naming the rollback command) when no candidate registry has a valid entry (a non-empty list of command hooks) for the event and matcher it was called with, when its payload temp file cannot be written, or on any error of its own. A damaged entry in one candidate MUST fall through to the next (the entry's rev file, registry.json, its backup, then registry.legacy.json for entries without a rev). For the other events it MUST exit 1 with the same note; their guards are notices and side effects.

#### Scenario: A damaged entry with a good backup
- **WHEN** registry.json holds `[null]` for the entry and its backup holds the guards
- **THEN** the backup's guards run and their deny stands

#### Scenario: Registry missing
- **WHEN** settings point at the dispatcher and the registry and its backup are missing
- **THEN** every Bash call is denied with a message naming `install-policy.py --hook-dispatcher off`

### Requirement: The installer folds only after parity and rolls back verbatim

`install-policy.py --hook-dispatcher on` MUST run the parity fixture against the home's own guards and MUST leave settings unchanged when it fails. `--hook-dispatcher off` MUST restore the per-hook config byte-for-byte as parsed JSON. Without the flag the installer MUST keep the current state, refold only groups the fixture passed on, and rerun the fixture when the dispatcher or an in-process guard changed, restoring the per-hook config when it fails.

#### Scenario: Round trip
- **WHEN** an operator runs `--hook-dispatcher on` then `--hook-dispatcher off`
- **THEN** the settings hooks equal the hooks before `on`

#### Scenario: A command two overlapping groups list
- **WHEN** a command sits in a folding group and in another group that may match the same tool and does not fold into the same entry (a regex matcher, or another key)
- **THEN** the folding group stays per-hook, so the command still runs once per call

#### Scenario: Uninstall keeps an adopted guard
- **WHEN** `--uninstall` runs over a home where install-policy installed the adopted `wide-scan-guard.sh`
- **THEN** the file stays in place (settings still register it) and only its ownership marker goes

#### Scenario: Folded entries name different revs
- **WHEN** the settings' dispatcher entries name more than one registry rev
- **THEN** the installer exits non-zero and writes nothing, so no entry is unfolded from another entry's rev

#### Scenario: Parity fails
- **WHEN** a guard answers differently on the two paths
- **THEN** `on` exits non-zero and settings and the registry are unchanged

### Requirement: Folds switch atomically

Each fold MUST be written as `registry.<rev>.json` before settings name that rev, and settings MUST switch every dispatcher entry in one write. Entries without a rev (written before revs) MUST keep the fold they were written with as `registry.legacy.json`.

### Requirement: Bash calls cost at most two processes

With the dispatcher installed over the live Studio guard set, a Bash tool call that no guard acts on MUST start at most two processes for its PreToolUse and PostToolUse hooks together. The count MUST be observed from the kernel (a kqueue watch on each hook process from before it runs, every fork counted), not inferred from the dispatcher's own records, and an unobserved count fails. Measured 2026-10-07: 2 with the session's cwd in a git repo; outside a git repo `rtk hook claude` runs `git rev-parse` and forks once per PATH entry ahead of git's directory (about 24 on Studio), both per-hook and through the dispatcher.

#### Scenario: Plain command
- **WHEN** a session runs `ls -la`
- **THEN** the PreToolUse dispatcher (exec'd into `rtk hook claude`) and the PostToolUse dispatcher are the only processes started
