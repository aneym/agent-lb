## MODIFIED Requirements

### Requirement: Runtime version status checks latest GitHub release

The service SHALL expose a dashboard-auth protected runtime version status API
that reports the running agent-lb version, the latest known GitHub release
version from the canonical public `aneym/agent-lb` repository when available,
whether an update is available, and the time of the latest lookup attempt. The
lookup MUST use the GitHub latest-release API for `aneym/agent-lb`. The
response MUST expose `releaseUrl:
"https://github.com/aneym/agent-lb/releases/latest"`. The lookup MUST be
cached in-process to avoid per-request GitHub traffic, and lookup failures MUST
NOT cause the API to fail.

#### Scenario: Latest release is newer than current version

- **WHEN** the running version is `1.19.0`
- **AND** the GitHub latest release tag is `v1.20.0`
- **THEN** the runtime version status reports `currentVersion: "1.19.0"`, `latestVersion: "1.20.0"`, and `updateAvailable: true`
- **AND** the response reports `releaseUrl: "https://github.com/aneym/agent-lb/releases/latest"`

#### Scenario: GitHub lookup uses the public release repository

- **WHEN** the runtime version service checks the latest GitHub release
- **THEN** it requests `https://api.github.com/repos/aneym/agent-lb/releases/latest`

#### Scenario: GitHub lookup fails

- **WHEN** the GitHub latest release lookup fails
- **THEN** the runtime version status API still returns the current version
- **AND** `updateAvailable` is `false`
- **AND** the response reports `releaseUrl: "https://github.com/aneym/agent-lb/releases/latest"`
