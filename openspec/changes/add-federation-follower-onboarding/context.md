# Context — add-federation-follower-onboarding

## Decisions

- **Status endpoint sits outside the peer-auth router.** The federation
  router 403s everything without the peer secret; a local operator checking
  health must not need that secret. Dashboard auth (passwordless localhost)
  matches every other `/api/*` operator surface.
- **Scheduler health is in-memory.** Last success/attempt/error and failure
  counts live on the scheduler singleton and reset on restart — that is the
  correct scope for "is the loop working right now"; durable history is what
  request logs and audit events are for.
- **Token via env/file only.** Argv leaks through `ps`; the current plist on
  the macbook already carries the token, so the installer reads/writes that
  same mechanism (launchd `EnvironmentVariables`), reusing the
  `install-service.sh` regeneration path that preserves operator-provided
  environment.
- **Success = observed mirror pull, not exit-code optimism.** The script's
  green path requires `mirror.last_success_at` to appear on the status
  endpoint, which proves peer reachability, token validity, and DB writes in
  one signal.

## Operational example (partner onboarding)

Prereqs: her Mac on the tailnet, repo cloned, `uv sync`,
`scripts/install-service.sh` run once.

```bash
export AGENT_LB_FEDERATION_TOKEN=<shared-token>   # from the pool operator
scripts/install-federation-follower.sh \
  --peer-url https://studio.tailf266ac.ts.net:2455 \
  --instance-id partner-mba
./clients/agent-lb-federation status   # instance id, peer, mirror health
```

Then wire clients normally (`scripts/install-claude-clients.sh`); her usage
appears on the owner under `partner-mba` via `GET /api/usage/instances`
(see add-federation-usage-attribution).
