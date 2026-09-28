## Why

Alex, 2026-09-28 ~19:30Z: "most of our default work should be routed to sonnet in general, just validated by sol and opus; scoping still done in opus." Sol implements only when Sonnet is really out: real 429s or usage-limit errors, or no Anthropic account that can serve Sonnet. No percentage or headroom threshold moves the work.

## What Changes

- The implement and mechanical chains lead with `sonnet-implementer` (sonnet-latest, effort high), then `gpt-implementer` (sol-latest, medium); opus-seat stays the pace-gated last resort.
- `sonnet-implementer` leaves `off_default`; its definition runs at effort high.
- The stage-effort bands for implement and mechanical allow high.
- A top-level `implement_default` names the seat that leads both chains. `route` applies it when it loads a table; setting it to `gpt-implementer` is the one-line revert.
- `verify-routing`'s implement-head rule accepts either default implementer at the head, and requires both in each chain.
- `route` treats a pool as exhausted only when `eligibleAccounts` is 0 (the status label decides only when no count is reported).
- Review is unchanged: Anthropic authors get codex-verifier (Sol xhigh), OpenAI authors get the Opus verifier.

## Impact

- `config/coding-agents/routing-table.json`, `agents/sonnet-implementer.md`, `ROUTING.md`, `verify-routing`, `clients/route`.
- Factory's fold template reads the head of the routed implement chain as its default implementer (factory repo, separate commit).
