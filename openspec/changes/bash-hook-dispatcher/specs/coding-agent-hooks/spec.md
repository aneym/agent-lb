## ADDED Requirements

### Requirement: The Bash dispatcher keeps every guard's decision
The dispatcher SHALL run each listed hook as `/bin/sh -c <command>` with the payload on stdin and the hook's own timeout (600 seconds when unset), killing a timed-out hook's process group and counting it as no decision. Its result SHALL match what Claude Code does with the same hooks registered directly: any exit 2 blocks with every blocker's stderr; JSON stdout that validates counts on every exit code; the permission decision is the strongest of deny, defer, ask and allow; `continue: false` with its stopReason, every additionalContext and every systemMessage are kept; and an applied updatedInput is one that a hook returned.

#### Scenario: A deny printed with exit 1
- **GIVEN** a listed hook that prints a JSON deny and exits 1
- **WHEN** the dispatcher runs
- **THEN** it returns that deny

#### Scenario: Malformed output beside a deny
- **GIVEN** one hook prints `{"hookSpecificOutput":"invalid"}` and another prints a JSON deny
- **WHEN** the dispatcher runs
- **THEN** it returns the deny

#### Scenario: Allow and ask
- **GIVEN** one hook returns allow and another returns ask
- **WHEN** the dispatcher runs
- **THEN** it returns ask

### Requirement: Gates skip only a pinned guard's silent fast path
The dispatcher SHALL skip a listed hook only when the gate copied from that guard's first check says the guard exits 0 with no output for the payload, and only while the installed guard file (or the inline command) has the pinned sha256. A changed, missing or unpinned guard, an unreadable payload, or a payload without a string command SHALL run every hook. The wide-scan guard SHALL always run.

#### Scenario: A guard changes after its pin
- **GIVEN** the Railway guard file differs from its pin
- **WHEN** a payload without the word railway arrives
- **THEN** the guard runs

### Requirement: The list fails closed
The dispatcher SHALL refuse every Bash call (exit 2) when its list is missing, empty, not a list, or holds an entry that is not a plain command hook. A hook entry registered directly and identical to a listed one SHALL run only there; a direct copy that differs, such as one with `if`, SHALL NOT stand in for the listed hook.

#### Scenario: An empty object as the list
- **GIVEN** the list file holds `{}`
- **WHEN** a Bash call arrives
- **THEN** the dispatcher exits 2 and names install-policy.py

### Requirement: The installer folds and restores without losing a guard
`install-policy.py` SHALL fold only Bash PreToolUse hooks whose keys are within type, command, timeout and statusMessage, register the dispatcher in the first folded hook's place with a timeout above the slowest listed hook, and leave every other handler registered directly. A later registration of a listed command SHALL replace that entry in place; a new command SHALL join after the listed ones. `--uninstall` SHALL put the listed hooks back in the dispatcher's place in list order. When the dispatcher is registered and the list is missing or empty, install and uninstall SHALL abort without writing.

#### Scenario: Round trip
- **GIVEN** a Bash group `[A, prompt, conditional, exec-form]` and a later Bash group `[B]`
- **WHEN** install and then uninstall run
- **THEN** the list is `[A, B]` while installed, and afterwards the Bash hooks are `[A, B, prompt, conditional, exec-form]`

#### Scenario: Missing list
- **GIVEN** the dispatcher is registered and the list file is gone
- **WHEN** install or uninstall runs
- **THEN** it exits non-zero and settings.json is unchanged
