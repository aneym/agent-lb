## Why

On 2026-10-06 Studio's syspolicyd got stuck and held every script exec at launch for over an hour, unchanged scripts included, while running the same file through an interpreter (`python3 <path>`) started in under 0.05 s. `seat run` launches `route` (a Python script) by path for resolve, reserve, heartbeat and release, so every Cursor and Devin seat on Studio sat at 0 CPU and the Grok fallback lanes were blocked. The seat agent definitions also start `seat` and `seat-submit` by path.

## What Changes

- `seat` runs a Python `route` (shebang names python) as `<seat's interpreter> <route> ...` for resolve, reserve, heartbeat and release. A non-Python `ROUTE_BIN` still runs by its own path. Argument order is unchanged.
- The cursor-seat, devin-seat, gpt-implementer and sonnet-implementer agent definitions call `python3 <path>` for `seat run` and `seat-submit`.

## Capabilities

### Modified Capabilities

- `routing-pools`: a seat's route calls do not depend on exec'ing the route script.

## Impact

- `clients/seat`, four agent definitions under `config/coding-agents/agents/`.
