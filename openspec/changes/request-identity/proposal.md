# Attribute request usage to a person and machine

## Why

Request logs show accounts and keys, but do not consistently identify the person
and machine that generated a request. Keyless clients and relayed requests are
especially hard to distinguish. Attribution must not mistake a key name or a
shared tailnet login for a person, or add a subprocess to the request path.

## What Changes

- Four nullable request-log fields record a short user handle, user source,
  machine handle, and machine source when a row is written. Historical rows
  stay null. Internal work is distinguished from client requests.
- Resolve client IP through the same resolver and settings as firewall/auth.
  Resolve person from validated member keys or the configured owner-machine
  allowlist; otherwise report `unknown`. Do not read or store login/email or
  persist client IP as identity.
- Capture HTTP and WebSocket identity in request context, with per-turn
  WebSocket updates and authenticated bridge forwarding. Ensure every log
  writer, including warmups, receives the appropriate identity.
- Refresh a small tailnet IP-to-node-handle cache in the background. Configure
  identity through flat environment settings; failures leave the last good
  cache intact and never block the request path.
- Expose attribution in the authenticated request-log API/dashboard and a
  bounded-window user/machine usage grouping on the existing authenticated
  dashboard usage API.

## Capabilities

### Modified Capabilities

- `database-migrations`: additive, nullable request-log attribution schema.
- `proxy-runtime-observability`: attribution, writer coverage, trust rules,
  request-log reads, and usage aggregation.
- `frontend-architecture`: visible request-log identity and provenance.

## Impact

- Affected code: request-log ORM and migration; identity resolver, middleware,
  cache and settings; proxy/Anthropic/stream/WebSocket/bridge writers; request
  log and usage API; dashboard recent-requests view.
- Federation per-instance daily rollups are not changed in this unit.
- Trust boundary: a loopback process can forge forwarding headers, matching
  the trust already granted to loopback by auth; Serve overwrites forwarding
  headers for tailnet peers. Claimed machine handles remain self-reported.
