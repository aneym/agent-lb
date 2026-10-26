# Tasks
- [x] Simulate per-account balances through resets to get b*, with plan factors, the 5-hour window capacity and the use-it-or-lose-it floor.
- [x] Tier ladder: step down at once, step up after two ticks, stale or incomplete input holds at behind or lower.
- [x] Report S*, Anthropic b* in output tokens over all reported pools' b* in output tokens.
- [x] Delete `pace_state`, pool history sampling, `_weekly_pace` and `weeklyPacePercent`; move every reader to the shadow pace.
- [x] Replay 14 days of usage history against the simulation (lane proof folder, Q1).
