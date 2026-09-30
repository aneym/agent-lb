# Stand-in readers

## Why
Pickers and people need to see when sessions run on stand-in models and when they expect to return to their intended models.

## What Changes
- Read the existing `/api/pools/stand-ins` endpoint once, with an empty fallback on failures or older builds.
- Include active stand-ins in `route pick` JSON and text output.
- Add `route stand-ins [--json]` for active and recent records.

## Impact
Affected specification: routing-pools. Affected code: clients/route.
No endpoint, routing policy, persisted state, or model selection changes.
