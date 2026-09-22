## ADDED Requirements

### Requirement: Empty brokers exit
The patched broker SHALL start an idle timer at startup and when the last socket closes. The default SHALL be 600000 milliseconds, overridden by a positive integer `CODEX_COMPANION_BROKER_IDLE_TIMEOUT_MS` no larger than 2147483647. Invalid values SHALL fail before starting the app-server.

#### Scenario: No clients ever connect
- **WHEN** a listening broker has no clients for the idle window
- **THEN** it SHALL shut down, remove its socket and pid file, and exit zero

#### Scenario: A connected client prevents expiration
- **WHEN** a client connects before expiration and remains connected past the idle window
- **THEN** the broker SHALL remain running and accept other clients

#### Scenario: Reconnection resets the window
- **WHEN** the last client disconnects and another connects before expiration
- **THEN** the old timer SHALL be cancelled and a full new window SHALL begin only after the final disconnect

### Requirement: Broker-owned process group cleanup
On POSIX the broker's app-server SHALL run in a private process group. Shutdown SHALL signal that group with SIGTERM and, if it survives three seconds, SIGKILL, including when the group leader has already exited. Concurrent shutdown requests SHALL share cleanup. CLI and JSON-RPC interfaces SHALL remain unchanged.

#### Scenario: A descendant ignores TERM
- **WHEN** shutdown begins and an app-server descendant in its process group ignores TERM
- **THEN** shutdown SHALL escalate to SIGKILL and the descendant SHALL no longer be running

### Requirement: Checked patch delivery
The apply script SHALL default to `~/.agent-lb/plugins/codex-plugin-cc`, allow `CODEX_PLUGIN_CC_DIR` to override it, and check the entire patch before applying. An already applied patch SHALL be reported as such. Incompatible or partially applied files SHALL produce nonzero exit and git's file/hunk diagnostics without changing any target file.

#### Scenario: Upstream drift
- **WHEN** a target hunk is incompatible
- **THEN** application SHALL fail without partial changes
