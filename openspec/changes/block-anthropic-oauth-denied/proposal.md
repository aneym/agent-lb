## Why
Anthropic quota recovery admits exhausted weekly accounts without a live refusal marker. Organization OAuth denials do not persist a routing block.

## What Changes
- Exclude future weekly exhaustion from Anthropic selection, including sticky affinity.
- Persist an organization OAuth denial as a subscription usability block and use existing failover.
- Clear only that refusal marker after a successful usage refresh.

## Impact
Account routing, subscription usability, Anthropic messages, and usage refresh. No schema or payload changes.
