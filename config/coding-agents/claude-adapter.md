<!-- agent-lb:coding-agent-routing:start -->

## Coding-agent routing

Canon: `~/.agents/policy/coding-agents/ROUTING.md`. Default to Claude Code,
vertically: the newest Opus drives (`cc` starts `opus[1m]`) and does the work
itself or through Claude subagents and teammates (`opus`, `sonnet`). Codex
(`sol-latest`) and Cursor (`grok-latest`) are optional extra capacity, not the
default: reach for them when the Claude pools are tight, for large parallel or
mechanical sweeps, or for a second opinion on a risky change. There is no
required hand-off and no required cross-vendor audit; verify work by running
it. Retired, and denied by the seat guard: Fable, the Codex Astra family, and
the gpt-5.6 generation and older. Name models by family alias, never by
version (`route resolve <alias>`, `route models`). Keep context lean: the
compaction window is 400k, write state to a handoff or ledger file rather than
carrying it in context, and do not poll. `route pools` shows live headroom;
dispatches and closeouts are logged in `~/.claude/logs/dispatch.jsonl`. Never
ask Alex for merges, deploys, rehearsed releases or infra fixes.

<!-- agent-lb:coding-agent-routing:end -->
