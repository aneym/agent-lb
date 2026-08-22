## ADDED Requirements

### Requirement: Blind-tunnel rate shaping

Each shim proxy process SHALL pace the aggregate bytes it relays across all blind CONNECT tunnels (combined directions) to a configured rate, defaulting to 10 Mbps, using earliest-departure-time scheduling so no tunnel can be starved by another tunnel's chunk-size pattern. The `CLAUDE_LB_TUNNEL_RATE_MBPS` environment variable SHALL override the rate, and a non-positive or non-finite value SHALL disable shaping entirely.

#### Scenario: Bulk tunnel download is paced

- **WHEN** a tunneled client downloads a large file through the shim with shaping at the default rate
- **THEN** the relay sustains approximately the configured rate instead of line rate

#### Scenario: Shaping disabled by environment

- **WHEN** the shim starts with `CLAUDE_LB_TUNNEL_RATE_MBPS` set to `0` or a non-finite value
- **THEN** blind tunnels relay unshaped and no pacing sleep occurs

### Requirement: API path exempt from shaping

The MITMed api.anthropic.com path SHALL NOT pass through the tunnel rate limiter. Small tunnel writes of at most 8 KB SHALL skip pacing sleeps while the booked backlog is within one burst window, while still debiting the schedule.

#### Scenario: API streaming unaffected by tunnel saturation

- **WHEN** a bulk tunnel download saturates the tunnel budget
- **THEN** intercepted API requests continue at full speed through the LB

#### Scenario: Small-chunk streams cannot dodge shaping

- **WHEN** a tunneled stream delivers its bytes exclusively in chunks of 8 KB or less
- **THEN** its sustained rate is still bounded by the configured budget once the burst window is consumed

### Requirement: Heavy tunnel attribution

The shim SHALL write a single-line stderr summary (host, port, bytes each direction, duration) for any blind tunnel that relayed at least 8 MB in total.

#### Scenario: Large download is attributable

- **WHEN** a tunnel closes after relaying 8 MB or more
- **THEN** the shim's stderr log names the destination host and the relayed volume
