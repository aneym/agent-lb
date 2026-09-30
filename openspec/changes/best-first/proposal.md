# Best-first routing

## Why
Alex approved routing-next at Balanced level on 2026-09-30: use the worker the quality and speed numbers rank best, preserving Claude usage for judgment and review. E16 (n=6) ranks Grok 4.7 low first for implementation.

## What Changes
- Guard only anthropic-general and anthropic-fable against low pace in the interim ladder; missing guarded_pools preserves all-pool pacing.
- Put Grok low first in implement, Composer first in mechanical and Sol low first in explore, with approved fallbacks behind each.
- Remove the unevaluated Grok medium implementation and Grok low exploration rungs.
- Remove the mechanical sol-low-rich promotion rung so Composer remains first; leave other rich promotion, baseline, the ladder switch and reviewer ordering unchanged.

## Impact
Affected specification: routing-pools. Affected code: clients/route and config/coding-agents/routing-table.json.
