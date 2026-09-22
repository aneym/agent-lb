## ADDED Requirements

### Requirement: Local CI gates an isolated exact commit

Agent LB MUST provide local run, status, show and doctor commands. Each run MUST
resolve a commit and execute the complete declared gate in its own detached
worktree. It MUST NOT execute tests against the shared editing checkout or the
live service database. PostgreSQL and kind resources MUST be uniquely owned by
the run and cleaned up after use.

#### Scenario: Dirty source checkout

- **WHEN** local CI is invoked from a checkout containing uncommitted edits
- **THEN** it tests the resolved committed SHA in an isolated worktree
- **AND** the receipt does not certify the uncommitted edits

### Requirement: Receipts fail closed

Local CI MUST retain exact SHA, machine identity, command, exit code, elapsed
time and hashed logs for each required leg. Status MUST exit successfully only
for the latest complete passing receipt for the requested SHA on the current
machine with verified logs and unchanged committed source. Missing, failed,
interrupted, tampered or incomplete evidence MUST NOT count as passing.

#### Scenario: One gate fails

- **WHEN** a gate command fails
- **THEN** independent remaining gates still run
- **AND** the overall receipt is red

#### Scenario: Receipt or logs unavailable

- **WHEN** status cannot validate required evidence for the requested SHA
- **THEN** it exits nonzero rather than reporting code-ready

### Requirement: Merge CI is local while publication remains separate

Hosted CI and hosted review-label workflows MUST NOT gate or run for Agent LB
merges. Exact-head local status and coordinator review MUST be the normal merge
requirements. Publication workflows MUST retain their separate artifact checks.
Windows-only verification MUST be identified separately from macOS proof.

#### Scenario: Post-merge verification

- **WHEN** a branch is merged
- **THEN** the merger runs the local full gate against main's exact commit
- **AND** reports its receipt without treating prior-head evidence as main proof
