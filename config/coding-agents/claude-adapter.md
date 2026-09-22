<!-- agent-lb:coding-agent-routing:start -->

## Coding-agent routing

Canon: `~/.agents/policy/coding-agents/ROUTING.md`; the seat table is
`~/.agent-lb/managed/coding-agents/routing-table.json` and `route pick <class>`
reads it. The newest Opus orchestrates, plans and reviews (`planner`,
`plan-reviewer` run on `model: opus`; `cc` starts `opus[1m]`), with the newest
Sol as the Codex-side alternative. Retired, and denied by the seat guard:
Fable, the whole Codex Astra family, and the gpt-5.6 generation and older.
Name models by family alias, never by version: `opus`/`sonnet` for Claude
seats, `sol-latest`/`terra-latest` for Codex, `grok-latest` for Cursor;
`route resolve <alias>` returns the newest served model and `route models`
lists them. Implementation goes to cheaper seats (Codex Terra when served,
Cursor Grok, GLM, Kimi), and every implementation closeout is audited by the
other vendor's stronger model before you accept it: Opus when Codex, Cursor,
GLM or Kimi wrote it, Sol when Anthropic wrote it (`route pick verify
--author-vendor <vendor>`). Fan out independent pieces to seats in parallel
(up to 4 live) and audit every closeout; never ask Alex for merges, deploys,
rehearsed releases or infra fixes. `route pools` shows
live headroom per pool; `route doctor` writes the live fallback chain. Every
dispatch and closeout is recorded in `~/.claude/logs/dispatch.jsonl`; defaults
change only from that record (`route learn`). A closeout is a claim; check
acceptance yourself.

<!-- agent-lb:coding-agent-routing:end -->
