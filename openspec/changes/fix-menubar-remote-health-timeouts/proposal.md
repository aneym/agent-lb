## Why

The macOS menu-bar client applies the loopback health-probe deadline to remote services. A healthy Tailnet service can therefore be classified as unreachable during a cold TLS connection even though it responds successfully moments later.

## What Changes

- Select the health-probe timeout envelope from the configured service locality.
- Preserve the existing 3-second request / 5-second resource deadline for loopback health probes.
- Give remote health probes the same bounded 15-second request / 20-second resource envelope already used by remote dashboard reads.
- Raise dashboard reads to a bounded 15-second request / 20-second resource envelope on both local and remote services so healthy cold database reads have measured headroom.
- Add focused regression coverage for the distinct health and dashboard timeout policies.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `macos-menubar`: Health probes use locality-aware bounded deadlines so cold remote TLS does not falsely mark a healthy service unreachable.

## Impact

- Affected code: `clients/macos-menubar/Sources/AgentLB/APIClient.swift`.
- Affected tests: macOS menu-bar API timeout policy tests.
- No server API, schema, dependency, or deployment changes.
