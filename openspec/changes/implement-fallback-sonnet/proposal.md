## Why

Orch-lab E12 (2026-09-28, E10 units, n=6 per batch, provisional) ranked the implementers by cost per accepted unit: Sol medium 6/6 at 379k fresh tokens and $0.86; Sonnet 5.5 high 8/12 at 565k and $2.76; Opus medium 4/6 at 683k and $3.76. The Sonnet-default steer of 19:30Z carried its own revert test (more than one unit below Sol, or higher cost per accepted unit), and E12 met both. Sol stays the default implementer. When Codex is truly empty (real 429s or usage-limit errors, not a low percentage), Sonnet 5.5 high stands in first, then opus-seat. This change replaces default-implementer-sonnet.

## What Changes

- `implement_default` is `gpt-implementer`; the implement and mechanical chains read gpt-implementer (sol-latest, medium), then sonnet-implementer (sonnet-latest, high), then opus-seat (pace-gated) or the mechanical capacity seats.
- `classes.implement.codex_empty_fallback` records the order (sonnet-implementer, then opus-seat), its trigger and the E12 evidence. `sonnet-implementer` stays out of `off_default`; its definition runs at effort high and says it is the Codex-empty fallback.
- `route` keeps two rules from the earlier change: `implement_default` moves its seat to the head of both chains, and a pool counts as exhausted only when `eligibleAccounts` is 0.
- ROUTING.md, the Claude adapter, planner and frontend-designer name Sol as the default and Sonnet as the Codex-empty stand-in.

## Impact

- `config/coding-agents/routing-table.json`, `ROUTING.md`, `claude-adapter.md`, `agents/{sonnet-implementer,planner,frontend-designer}.md`, `tests/unit/test_route_cli.py`.
- Factory's fold template re-seats Sol or Luna after two infra failures to sonnet-implementer, then opus-seat (factory e576cee).
