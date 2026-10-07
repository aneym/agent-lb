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

### Requirement: Half-installed states fail closed

The dispatcher MUST refuse a PreToolUse call (exit 2 and a JSON deny naming the rollback command) when its registry is unreadable or has no entry for the event and matcher it was called with. For the other events it MUST exit 1 with the same note.

#### Scenario: Registry missing
- **WHEN** settings point at the dispatcher and the registry and its backup are missing
- **THEN** every Bash call is denied with a message naming `install-policy.py --hook-dispatcher off`

### Requirement: The installer folds only after parity and rolls back verbatim

`install-policy.py --hook-dispatcher on` MUST run the parity fixture against the home's own guards and MUST leave settings unchanged when it fails. `--hook-dispatcher off` MUST restore the per-hook config byte-for-byte as parsed JSON. Without the flag the installer MUST keep the current state, refold only groups the fixture passed on, and rerun the fixture when the dispatcher or an in-process guard changed, restoring the per-hook config when it fails.

#### Scenario: Round trip
- **WHEN** an operator runs `--hook-dispatcher on` then `--hook-dispatcher off`
- **THEN** the settings hooks equal the hooks before `on`

#### Scenario: Parity fails
- **WHEN** a guard answers differently on the two paths
- **THEN** `on` exits non-zero and settings and the registry are unchanged

### Requirement: Bash calls cost at most two processes

With the dispatcher installed over the live Studio guard set, a Bash tool call that no guard acts on MUST start at most two processes for its PreToolUse and PostToolUse hooks together.

#### Scenario: Plain command
- **WHEN** a session runs `ls -la`
- **THEN** the PreToolUse dispatcher (exec'd into `rtk hook claude`) and the PostToolUse dispatcher are the only processes started
