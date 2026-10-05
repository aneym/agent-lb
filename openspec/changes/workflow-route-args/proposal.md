# Routing inside Claude Code Workflow scripts

## Why
Alex asked on 2026-10-05 (12:07 ET) whether routing and model selection keep working through Claude Code native workflows, and to make them work by default. Workflow scripts have no shell, so a script cannot run `route pick`, and measured behavior on 2026-10-05 showed three gaps:
- The Agent seat guard (PreToolUse on `Agent`) never sees workflow `agent()` calls: workflow-only seats such as host-relay have 365 closeout rows and no dispatch rows since 2026-10-04 in `~/.claude/logs/dispatch.jsonl`. Only `workflow-seat-guard` runs, once, on the script text.
- Options a script reads from Workflow `args` are invisible to that static scan.
- Each seat kind takes a model and effort differently: the ccgpt bridge locks reasoning effort from the model name suffix and ignores the request's own effort, forwarders take their CLI model from the brief, and Claude seats take `opts.model`/`opts.effort` over their frontmatter. Scripts that hard-code `agentType: 'verifier'` ran Opus 3,254 times while the interim ladder picks Sonnet high.

## What Changes
- `route workflow-args` prints one JSON document: for every chain or ladder class, the live pick as `{opts, brief}` ready for `agent()`, with mapped fallbacks and auditor, plus `review_for` keyed by author vendor and Claude Code's per-workflow `agent_cap`.
- `workflow-seat-guard` also denies a Workflow call whose `args` carry a retired `model` beside a seat key.
- ROUTING.md "Workflows" states the script pattern and the measured behavior.

## Impact
Affected specification: routing-pools. Affected code: clients/route (new read-only subcommand; pick logic unchanged), config/coding-agents/hooks/workflow-seat-guard.py, config/coding-agents/ROUTING.md.
