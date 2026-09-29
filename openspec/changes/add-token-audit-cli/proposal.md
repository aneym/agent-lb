# Token audit CLI

## Why
Request receipts are the authoritative token ledger, but spend and attribution
currently require bespoke queries. A read-only CLI makes this repeatable.

## Changes
Add `audit tokens` with UTC windows, provider-correct token accounting, observed
and recomputed pricing, local session attribution, pane snapshots, waste candidates,
and text/JSON/self-contained HTML reports. No request-path or database mutations.

## Limits
Parent session receipts cannot prove subagent-stage ownership. Waste candidates
are overlapping heuristics, not proven avoidable spend. Rolling windows are not
individual account reset windows. OpenSpec CLI is unavailable: validation pending.
