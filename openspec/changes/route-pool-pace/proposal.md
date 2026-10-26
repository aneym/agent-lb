# route-pool-pace

## Why
Council A (2026-10-26) ruled that pacing is one computation in agent-lb. The client-side `pace_state`, its local pool history, and the averaged `weeklyPacePercent` each disagreed with the per-account simulation, and they misread staggered and provider-side resets.

## What Changes
- agent-lb computes each multi-account pool's sustainable burn b* by simulating every account through its resets (`app/modules/pools/pace_curve.py`), and serves it at `GET /api/pools/pace`, on `/api/pools` as each pool's `pace`, and through `agent-lb pace --json` and `route pace --json`.
- The tier ladder (critical, behind, on_pace, ahead, rich) is reported in shadow mode only; nothing is enforced.
- Removed: the client `pace_state`, `~/.claude/route-pool-history.jsonl` sampling, the server `_weekly_pace` average, and the `weeklyPacePercent` field. `min_pace` on a rung becomes `min_tier`, reported but not enforced.
