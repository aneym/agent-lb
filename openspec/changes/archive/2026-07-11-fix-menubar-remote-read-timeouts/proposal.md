## Why

The macOS menu-bar client applies a three-second read timeout to both local and
remote services. A live MacBook request to Studio's usage summary took 3.68
seconds, so healthy cached data is repeatedly accompanied by misleading
"couldn't load — retry" rows.

## What Changes

- Give remote dashboard reads a latency budget appropriate for tailnet access
  while preserving the fast local failure envelope.
- Centralize and test the timeout policy so future client changes cannot
  silently restore the local-only assumption.
- Keep genuine completed failures visible and retain the existing
  cancellation-neutral section error semantics.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `macos-menubar`: remote dashboard reads must tolerate normal tailnet latency
  without hiding genuine failures.

## Impact

- Affected code: macOS menu-bar API client and focused Swift tests.
- No server API, database, layout, or account-routing contract changes.
- Deployment: rebuild and relaunch the signed menu-bar bundle on Studio and
  MacBook; verify the MacBook against Studio's tailnet endpoint.
