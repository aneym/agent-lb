# add-open-factory-routing

## Why
Open Factory (OF) chose a seat per launched process from a hardcoded catalog that still
listed retired models, and wrote a per-project ledger nothing read. The real router is
`clients/route` with the routing table. OF must route per call through it, let a decider
(Jev) pick among seats the host has vetted, keep the move with the host, and record each
decision where `route report` and the eval can read it.

## What Changes
- `route menu [--class C] [--json]`: every seat runnable now per class, the excluded seats
  with a reason, and per-pool headroom, weekly pace and the next 5-hour and weekly resets.
  `route pick` and `route menu` share one eligibility function.
- `open-factory route --class C "<task>"`: menu, decider (`jev pick` or static), host
  re-check against a fresh menu, fallback to `route pick`, then the driver; one
  `of_decision` row per decision in `~/.claude/logs/dispatch.jsonl`.
- `open-factory` drops `catalog.json`, `recommend_driver()` and the per-project ledgers;
  `start` launches the Opus driver (`cc` arguments) with the live menu in the prompt;
  `report` summarizes `of_decision` rows.
