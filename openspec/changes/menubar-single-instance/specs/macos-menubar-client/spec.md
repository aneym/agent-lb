## ADDED Requirements

### Requirement: At most one menubar instance per user session

The menubar app MUST enforce single-instance execution per user session per machine
via an OS-level lock acquired before any UI or polling starts, regardless of which
bundle copy is launched or how it is started. A duplicate launch MUST exit cleanly
(status 0) after logging, leaving the running instance untouched. Lock acquisition
MUST tolerate the brief overlap of a supervised restart without stranding the user
with zero running instances.

#### Scenario: Duplicate launch exits immediately

- **GIVEN** a running menubar instance holding the single-instance lock
- **WHEN** a second copy of the app is launched (from any bundle path)
- **THEN** the second process writes a single stderr line and exits with status 0
- **AND** the first instance and its status item are unaffected

#### Scenario: Supervised restart survives the handover overlap

- **GIVEN** the LaunchAgent restarts the app with `launchctl kickstart -k`
- **WHEN** the new process starts while the old one is still shutting down
- **THEN** the new process retries lock acquisition briefly and takes over once the old process releases it

#### Scenario: Crash does not wedge the lock

- **WHEN** the running instance crashes or is killed
- **THEN** the OS releases the lock with the process
- **AND** the next launch acquires it without manual cleanup
