# route-pool-pace

## Why
Pool pacing must identify both quota that will run out before reset and quota that will remain unused at reset. Live remaining percentages and the last day's burn reflect shared account usage better than this client's request logs.

## What Changes
- Add per-pool pace states and projection fields to `route pools --json`, and a pace column to text output.
- Prefer explicit daily burn or local remaining-percentage history over the pool's weekly or monthly pace indicator.
- Sample successful pool fetches in `route pools`, `route pick`, `route menu` and `route doctor --write` into a bounded local history file.
- Default `policy.pace.rich_leftover` to 15 in the table loader.
- Keep `pool_pace`, `min_pace` and routing eligibility unchanged; consuming the new states in routing is separate work.
