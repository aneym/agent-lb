## 1. Routing correction

- [x] 1.1 Add a regression test proving recovered primary headroom is routable despite an active historical extra-usage tripwire.
- [x] 1.2 Keep a fresh extra-usage tripwire authoritative while allowing a newer primary-headroom snapshot to reconcile it.

## 2. Validation and live proof

- [x] 2.1 Run the targeted proxy tests, ruff, import checks, and strict OpenSpec validation.
- [x] 2.2 Probe all configured Anthropic accounts with appropriate model scopes and record exact outcomes.
- [x] 2.3 Restart the live service and prove a fresh Claude Code routing request completes without the stale-tripwire hold.
- [ ] 2.4 Commit and push the validated change to `origin/main`.
