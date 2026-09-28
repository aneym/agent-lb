## ADDED Requirements

### Requirement: Per-session shim ready files are pruned on launch

The Claude launcher SHALL remove its `cc-*.proxy` and `rails-desktop-*.proxy` ready files when their mtime is older than 24 hours, or when their mtime is older than ten seconds and the file names an invalid port or a port that cannot be reached on loopback. A reachable port SHALL preserve a ready file within the 24-hour limit. The launcher MUST NOT prune shared `desktop.proxy` files, temporary `.proxy.tmp` files, or any other name.

#### Scenario: Dead per-session shim leaves a ready file

- **WHEN** a Claude Code or Rails desktop session proxy is killed and leaves a ready file older than ten seconds
- **AND** the port in the ready file is invalid or cannot be reached on loopback
- **THEN** the next launcher cleanup removes the file

#### Scenario: Reachable per-session shim survives short grace

- **WHEN** a per-session ready file is older than ten seconds but newer than 24 hours
- **AND** the port in the ready file accepts a loopback connection
- **THEN** the launcher keeps the file

#### Scenario: Only launcher-owned ready files are touched

- **WHEN** the launcher cleans up a shim directory containing an old shared `desktop.proxy` or temporary `.proxy.tmp` file
- **THEN** those files remain untouched
