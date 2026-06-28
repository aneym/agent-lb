## MODIFIED Requirements

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
