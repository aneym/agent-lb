# Codex pace guard and Grok hold

## Why
On 2026-10-05 the five OpenAI accounts had 25, 14, 10, 10 and 6 percent of the week left with resets on 10-09, and every Grok seat run on both Cursor accounts failed with "out of usage" while Composer kept working. The orchestrator asked for Codex to last until the reset, with Sol kept for reviewing Claude-authored work.

## What Changes
- Add openai-codex to policy.pace.guarded_pools in the canonical table. Sol worker rungs go soft while Codex is behind pace and return when it is not.
- Close the gate on the interim grok-medium and grok-low rungs until Grok has usage again.
- Supersede best-first's "Codex runs low but is eligible" and "Cursor models becomes empty" scenarios and its guarded-pool list for the canonical table.

## Impact
Affected specification: routing-pools. Affected code: config/coding-agents/routing-table.json and the tests that pin its head rungs; clients/route is unchanged.
