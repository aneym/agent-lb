# Tasks — add-federation-follower-onboarding

- [x] 1. Scheduler health fields: last_success_at, last_attempt_at,
      consecutive_failures (exists), last_error, usage-push last
      success/error; expose the scheduler instance to the API layer.
- [x] 2. `GET /api/federation/status` (dashboard auth, separate router or
      per-route dependency): schema per delta spec; owned vs mirrored counts
      from the accounts table; works unconfigured.
- [x] 3. `scripts/install-federation-follower.sh`: flag parsing, token via
      env/file only, plist env injection reusing install-service.sh
      regeneration, restart, poll status until mirror success (bounded
      timeout), `--print` (token redacted), `--uninstall` (federation keys
      only), idempotent.
- [x] 4. `clients/agent-lb-federation status`: read the new endpoint; show
      instance id, peer, mirror health, account counts.
- [x] 5. `GETTING-STARTED.md`: "Join an existing pool (federation follower)"
      section (prereqs: tailnet, clone, uv sync, install-service.sh; then
      the follower script; then client wiring).
- [x] 6. Tests: status endpoint healthy/unconfigured/no-token-leak; scheduler
      health field updates on success and failure paths.
