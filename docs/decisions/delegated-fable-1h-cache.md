# Keep delegated Fable threads and Recruiter Opus sessions on 1h prompt caching

Date: 2026-11-25

## Evidence

The 2026-11-25 24h Anthropic quota forensics audit (`claude-quota-big-spenders-forensics.md`) measured `agent-lb` Postgres `request_logs` and found `_short_gap()` in `app/modules/proxy/anthropic_cache_policy.py` downgrading every `thread:delegated-task:` Fable thread (`claude-fable-5-2`) from `1h` to `5m` prompt caching:

- Depth-1 delegated Fable threads are long-lived coordinators carrying 160K–190K context windows that wait >5 minutes on depth-2 children 15–54 times per session.
- Across 24 hours on `ax102`, 96.8% of Fable requests (12,563 delegated requests) wrote `5m` cache, incurring 565 cold wakeups after 5–60 minute idle gaps (~$969.55 list-price quota proxy).
- Top-level `mcp:launch:` Fable sessions on `ax42` and Mac Studio stayed on `1h` and paid $0 in 5m cold-wake tax.
- Recruiter Opus T3 sessions also live 20–28h+ with >5m gaps, while true one-shot routines already hit `sdk-cli` or `rails-runner`.

## Decision

Reserve the `5m` cache tier in `_short_gap()` strictly for seat `rails-runner` alongside the `sdk-cli` one-shot entrypoint rule (`ONE_SHOT_ENTRYPOINTS`). Delegated Fable threads (`thread:delegated-task:`) and Recruiter Opus sessions stay on the `1h` session cache tier.
