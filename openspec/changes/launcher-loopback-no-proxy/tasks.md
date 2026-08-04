# Tasks

- [x] Append loopback hosts to `NO_PROXY`/`no_proxy` at the point `claude-lb-launch` exports `HTTPS_PROXY`/`https_proxy` (preserve existing entries, skip duplicates).
- [x] Validate: `python3 -m py_compile clients/claude-lb-launch` passes.
- [x] Validate: `CLAUDE_LB_DRY_RUN=1 ./clients/claude-lb-launch` completes and prints the launch command.
- [x] Validate: merge logic preserves pre-existing `NO_PROXY=api.anthropic.com` → `api.anthropic.com,127.0.0.1,localhost`.
