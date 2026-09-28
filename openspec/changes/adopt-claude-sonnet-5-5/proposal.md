# adopt-claude-sonnet-5-5

## Why

Claude Sonnet 5.5 (`claude-sonnet-5-5`) shipped on 2026-09-28 at Sonnet 5's price. Claude Code 2.1.284 still maps its `sonnet` alias to `claude-sonnet-5`, so every seat that names `sonnet` or `sonnet-latest` keeps running the old model.

## What Changes

- `install-policy.py` writes `env.ANTHROPIC_DEFAULT_SONNET_MODEL = "claude-sonnet-5-5"` into `~/.claude/settings.json` on install and removes it on uninstall when it still holds that value.
- `claude-sonnet-5` joins the routing table's `retired` list, so the seat guard refuses an explicit pin of it.
- `agent-defs-doctor` accepts `claude-sonnet-5-5` in agent frontmatter.
- The Open Factory Sonnet arm and the README example name `claude-sonnet-5-5`.

## Impact

- Affected code: `config/coding-agents/install-policy.py`, `config/coding-agents/routing-table.json`, `config/coding-agents/ROUTING.md`, `clients/agent-defs-doctor`, `clients/open-factory/evals/of-work/arms.json`, `clients/open-factory/harbor/*`, `README.md`.
- No request-path, pricing or account change: the proxy passes the model through, and `claude-sonnet-5*` already prices at Sonnet 5 rates.
