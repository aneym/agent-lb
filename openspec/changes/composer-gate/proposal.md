# Composer hold

## Why
On 2026-10-05 Composer seat runs failed with "You're out of usage" on both Cursor accounts: cursor-main on Studio and cursor-gmail on ax42. Grok was already gated for the same reason. Composer still led implement and mechanical and was second for explore, so live picks spent a failed attempt before reaching Devin.

## What Changes
- Close the gate on the interim composer rungs for implement, mechanical and explore until Cursor has usage again.
- Live picks then go Devin SWE first, Sol behind it while Codex is behind pace, then the Sonnet stand-in.
- Cursor `auto` is not added as a rung: nothing shows that it stays inside the plan with on-demand billing off.
- Supersede codex-pace-guard's scenarios that expect composer as the pick for the canonical table.

## Impact
Affected specification: routing-pools. Affected code: config/coding-agents/routing-table.json and the tests that pin its head rungs; clients/route is unchanged.
