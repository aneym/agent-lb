# deployment-networking Delta

## ADDED Requirements

### Requirement: Always-up TCP front makes app restarts invisible to clients

The repository MUST provide an always-up localhost TCP front
(`scripts/agent-lb-front.mjs`, installed as the `com.aneyman.agent-lb-front`
LaunchAgent by `scripts/install-front.sh`) that owns the public localhost port
(default 2455) and pipes raw TCP to the app on an internal port (default
2457). While the app port is not accepting connections, the front MUST hold
new client connections and retry the upstream connect for a configurable
window (`AGENT_LB_FRONT_HOLD_MS`, default 180000 ms) instead of refusing,
so reverse proxies in front of the public port (e.g. tailscale serve) do not
convert an app restart into `502 Bad Gateway`. The front MUST NOT parse or
rewrite the proxied byte stream, and MUST close both sides of a pair when
either side closes.

#### Scenario: Connection arriving during an app restart completes

- **GIVEN** the app port is down because the service is restarting
- **WHEN** a client connects to the front and the app begins accepting within
  the hold window
- **THEN** the client's request completes normally after the app is up

#### Scenario: Extended outage still surfaces as a failure

- **GIVEN** the app port stays down beyond the hold window
- **WHEN** a client connection has been held the whole window
- **THEN** the front closes the connection instead of holding it forever

#### Scenario: Healthy pass-through is transparent

- **GIVEN** the app is accepting on the internal port
- **WHEN** a client connects through the front
- **THEN** bytes flow both directions unmodified, including for streaming and
  websocket traffic
