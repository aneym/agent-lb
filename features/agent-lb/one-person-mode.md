---
id: one-person-mode
product: agent-lb
owner: factory
status: live
covers: []
paths: ["app/solo_cli.py", "app/solo_env.py", "app/solo_app.py", "app/solo_routes.py", "app/solo_headroom.py", "app/solo_headroom_sender.py", "app/claude_signin.py", "app/claude_signin_import.py", "docs/one-person-mode.md", "features/agent-lb/one-person-mode.md"]
proofs: []
---
# One-person mode

## Sub-features
`agent-lb solo serve` runs agent-lb over one person's own plans on 127.0.0.1, with no dashboard and no federation, and posts a percent headroom summary to a configured Rails endpoint. `agent-lb solo import-claude` imports that person's Claude Code sign-in from its credential file or from stdin. `agent-lb solo headroom` prints the summary.

## How to get to it
Install agent-lb, run `agent-lb solo import-claude`, then `agent-lb solo serve`. See `docs/one-person-mode.md`.

## Driving it
Point a client at `http://127.0.0.1:2456`. Set `AGENT_LB_SOLO_HEADROOM_URL` and `AGENT_LB_SOLO_HEADROOM_TOKEN_FILE` to post headroom.

## Gotchas
The mode refuses the shared `~/.agent-lb` data dir. macOS keeps the Claude sign-in in the keychain; agent-lb never reads it, so export it and pass `--stdin`.
