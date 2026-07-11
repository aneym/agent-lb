# deployment-installation Specification

## Purpose

Define installation modes and smoke-test expectations so the Helm chart remains portable across supported deployments.
## Requirements
### Requirement: macOS service restart completion is readiness-driven and timed

The macOS LaunchAgent installer MUST wait for the previous localhost listener to release, MUST retry bootstrap using bounded state-driven backoff, and MUST require `/health/ready` before reporting successful installation. Its readiness deadline MUST be bounded, operator-configurable, and long enough by default for an observed loaded-host cold start. It MUST report elapsed timing for the restart phases without exposing environment variable values or credentials.

#### Scenario: Existing service restarts promptly

- **WHEN** the existing LaunchAgent releases its listener and launchd accepts bootstrap before the retry deadline
- **THEN** the installer proceeds without an unconditional fixed cooldown
- **AND** it reports bootout, bootstrap, process startup, and readiness elapsed timing

#### Scenario: Process is live but not ready

- **WHEN** the replacement process responds to liveness but `/health/ready` remains unavailable
- **THEN** the installer does not report success
- **AND** it continues bounded readiness polling or exits with a diagnostic naming the service log

### Requirement: Helm chart is organized around install modes

The Helm chart MUST document and support three primary install modes: bundled PostgreSQL, direct external database, and external secrets. These install contracts MUST be portable across Kubernetes providers without requiring provider-specific chart forks.

#### Scenario: Bundled mode values exist

- **WHEN** a user wants a self-contained install
- **THEN** the chart provides a bundled mode values overlay with bundled PostgreSQL enabled

#### Scenario: External DB mode values exist

- **WHEN** a user wants to install against an already reachable PostgreSQL database
- **THEN** the chart provides an external DB values overlay and accepts direct DB URL or DB secret wiring

#### Scenario: External secrets mode values exist

- **WHEN** a user wants to source credentials from External Secrets Operator
- **THEN** the chart provides an external secrets values overlay that keeps migration and startup behavior fail-closed

### Requirement: Helm install modes are smoke-tested

The project MUST run automated Helm smoke installs for the easy-setup install modes in CI.

#### Scenario: Bundled and external DB modes are smoke tested

- **WHEN** CI runs Helm smoke installation checks
- **THEN** it installs the chart on a disposable Kubernetes cluster in bundled mode
- **AND** it installs the chart on a disposable Kubernetes cluster in external DB mode
- **AND** both installs reach a healthy testable state

### Requirement: Helm support policy is pinned to modern Kubernetes minors

The chart MUST declare a minimum supported Kubernetes version of `1.32`, and CI MUST validate chart rendering against a `1.35` baseline instead of older legacy minors.

#### Scenario: Chart metadata declares the minimum supported version

- **WHEN** a user inspects the chart metadata and README
- **THEN** the documented minimum supported Kubernetes version is `1.32`

#### Scenario: CI validates the modern baseline

- **WHEN** CI runs Kubernetes schema validation and kind-based smoke installs
- **THEN** the validation set includes Kubernetes `1.35`
- **AND** pre-`1.32` validation targets are not treated as the support baseline

### Requirement: Application data directory resolution is configurable and container-aware

The application MUST resolve its default data directory from operator intent before container heuristics. A non-empty `AGENT_LB_DATA_DIR` value MUST be the highest-priority data directory override. When no override is configured, an existing `$HOME/.agent-lb` directory MUST remain preferred even if the process detects that it is running inside a container. The container data directory (`/var/lib/agent-lb`) MUST be used only when no override is configured, the home data directory does not already exist, and container detection is true.

#### Scenario: Explicit data directory override wins

- **GIVEN** `AGENT_LB_DATA_DIR` is configured to a non-empty path
- **WHEN** application settings are loaded
- **THEN** the configured path is used as the data directory
- **AND** the container detection result does not override it

#### Scenario: Existing home data is reused inside an interactive container

- **GIVEN** `AGENT_LB_DATA_DIR` is not configured
- **AND** `$HOME/.agent-lb` already exists
- **AND** container detection is true
- **WHEN** application settings are loaded
- **THEN** `$HOME/.agent-lb` is used as the data directory
- **AND** `/var/lib/agent-lb` is not selected

#### Scenario: Container default is preserved when no home data exists

- **GIVEN** `AGENT_LB_DATA_DIR` is not configured
- **AND** `$HOME/.agent-lb` does not exist
- **AND** container detection is true
- **WHEN** application settings are loaded
- **THEN** `/var/lib/agent-lb` is used as the data directory

#### Scenario: Related default paths follow the resolved data directory

- **GIVEN** the resolved data directory differs from the module-import default
- **AND** the database URL, encryption key file, conversation archive directory, and response-create dump directory are not explicitly configured
- **WHEN** application settings and proxy dump helpers are used
- **THEN** the default SQLite database URL points at `<data-dir>/store.db`
- **AND** the default encryption key file points at `<data-dir>/encryption.key`
- **AND** the default conversation archive directory points at `<data-dir>/conversation-archive`
- **AND** oversized response-create dumps are written under `<data-dir>/debug/response-create-dumps`

#### Scenario: Explicit related path overrides are preserved

- **GIVEN** `AGENT_LB_DATA_DIR` is configured
- **AND** one or more related paths such as `AGENT_LB_DATABASE_URL`, `AGENT_LB_ENCRYPTION_KEY_FILE`, or `AGENT_LB_CONVERSATION_ARCHIVE_DIR` are explicitly configured
- **WHEN** application settings are loaded
- **THEN** each explicitly configured related path keeps its configured value
- **AND** only omitted related paths derive from the resolved data directory

### Requirement: macOS LaunchAgent install preserves runtime configuration

The macOS LaunchAgent installer MUST preserve existing operator runtime configuration when regenerating the `com.aneyman.agent-lb` plist. Existing environment variables, command-line arguments after the agent-lb binary, and LaunchAgent resource limits MUST survive reinstall. Installer-owned defaults MAY fill missing values, including metrics defaults, but MUST NOT remove existing database, dashboard-auth, trusted-proxy, OAuth, or resource-limit settings. The port-conflict guard MUST evaluate the localhost service endpoint rather than unrelated Tailnet listeners on the same public port.
The default local service runtime MUST include the Prometheus client required by installer-owned metrics defaults.

#### Scenario: Existing Postgres and trusted proxy settings survive reinstall

- **GIVEN** an existing `com.aneyman.agent-lb` LaunchAgent plist contains
  `AGENT_LB_DATABASE_URL`, `AGENT_LB_DASHBOARD_AUTH_MODE`,
  `AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS`, custom `--host` and `--port`
  arguments, and file descriptor resource limits
- **WHEN** `scripts/install-service.sh` regenerates the plist
- **THEN** the generated plist retains those existing values
- **AND** it adds missing metrics defaults without replacing explicit metrics
  overrides
- **AND** the default runtime dependencies include the metrics scraper library

#### Scenario: Tailnet listener does not block local restart

- **GIVEN** Tailscale or another remote forwarder listens on port `2455` for a
  Tailnet address
- **AND** no unrelated process owns `127.0.0.1:2455`
- **WHEN** the macOS installer evaluates whether it may bootstrap agent-lb
- **THEN** the Tailnet listener alone does not trigger the local port-conflict
  guard

### Requirement: Watchdog revives an un-bootstrapped launchd service

The repository MUST version the host watchdog script at `scripts/watchdog.sh` as
the canonical source for `~/.agent-lb/bin/watchdog.sh`. When the main launchd
job is not bootstrapped, the service plist exists, and no pause file is present,
the watchdog MUST re-bootstrap the service after a configurable number of
consecutive ticks (default 2). The pause file MUST be the only signal that
downtime is intentional.

#### Scenario: Failed deploy leaves the service booted out

- **GIVEN** the launchd job is not bootstrapped and no pause file exists
- **WHEN** the watchdog observes the missing job for 2 consecutive ticks
- **THEN** it bootstraps the service from the plist

#### Scenario: Single missing tick does not trigger revival

- **GIVEN** the launchd job is not bootstrapped and no pause file exists
- **WHEN** the watchdog observes the missing job for the first tick
- **THEN** it records the observation without bootstrapping

#### Scenario: Pause file marks downtime as intentional

- **GIVEN** the pause file exists
- **WHEN** the watchdog runs while the job is not bootstrapped
- **THEN** it exits without bootstrapping or kickstarting
