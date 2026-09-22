## Why
Detached Codex companion brokers survive abandoned sessions and retain app-server/MCP processes. The upstream plugin is read-only to this project.

## What Changes
- Own a two-file upstream patch and atomic, checked application script in agent-lb.
- Exit an empty broker after ten minutes (configurable); cancel idle shutdown while any client is connected.
- Isolate the broker's app-server process group and reap it with TERM followed by KILL after three seconds.
- Verify with real broker sockets and PIDs in an isolated plugin copy.

## Capabilities
### New Capabilities
- `codex-plugin-patch`: Reapplicable broker idle-exit and process-group cleanup patch.

### Modified Capabilities
None.

## Impact
Only new agent-lb patch, script, tests, and OpenSpec files. No upstream commit or supervisor. Existing brokers are not signalled or upgraded in place.
