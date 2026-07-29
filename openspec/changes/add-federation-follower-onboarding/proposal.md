# Add federation follower onboarding

## Why

Joining a new machine to an existing federated pool is currently hand-work:
edit the launchd plist environment by hand, restart, and eyeball logs to see
whether mirror pulls succeed. The federation CLI's `status` command cannot
even report the local instance id or mirror health because no endpoint
exposes them. Onboarding a non-operator machine (e.g. a partner's Mac) needs
to be one idempotent command with a machine-checkable success signal.

## What Changes

- New `GET /api/federation/status` under dashboard authentication (not peer
  auth): local instance id, peer configuration, mirror scheduler health
  (last success/attempt, consecutive failures, last error), usage-report push
  health, and owned/mirrored account counts.
- New `scripts/install-federation-follower.sh`: injects the federation
  environment (`AGENT_LB_LOCAL_INSTANCE_ID`, `AGENT_LB_FEDERATION_TOKEN`,
  `AGENT_LB_FEDERATION_PEER_URL`, `AGENT_LB_FEDERATION_MIRROR_INTERVAL_SECONDS`)
  into the launchd service, restarts it, and polls the status endpoint until
  the first mirror pull succeeds. Idempotent; `--print` preview;
  `--uninstall` removes the federation environment. The token is never taken
  from argv.
- `clients/agent-lb-federation status` reads the new endpoint.
- `GETTING-STARTED.md` gains a "Join an existing pool (federation follower)"
  section.

## Impact

- Affected specs: `instance-federation`, `deployment-installation`
- Affected code: `app/modules/federation/` (status api + scheduler health
  fields), `scripts/install-federation-follower.sh` (new),
  `clients/agent-lb-federation`, `GETTING-STARTED.md`, tests.
