# Add federation account push

## Why

Nate runs his own agent-lb and wants some of his accounts to also route on
Alex's Studio LB. The existing federation mirror is a pull: the follower calls
the owner's `GET /api/federation/mirror`. Studio cannot open connections to
Nate's machine (Studio is shared with Nate on Tailscale, and a Tailscale grant
lets shared users reach only Studio `tcp:2455`), so Studio cannot pull. Nate's
LB has to push.

Nate's LB stays the owner and the only refresher of those accounts. Studio
routes on the pushed access tokens and never refreshes them, exactly like a
pull-mirrored row today.

## What Changes

- Receiver (Studio): `POST /api/federation/push` on the existing
  non-dashboard federation router. The caller is identified by Tailscale
  identity: the LB resolves the real client IP (trusted-proxy aware, because
  `tailscale serve` forwards it), runs `tailscale whois --json <ip>`, and
  matches `UserProfile.LoginName` against a sources file
  (`~/.agent-lb/federation-push-sources.json`). No bearer token, no proxy API
  key, and team mode's dashboard gate does not apply. The first accepted push
  binds the source to its `instance_id` (`~/.agent-lb/federation-push-state.json`).
- Each pushed account is upserted as a mirrored row
  (`owner_instance` = the sender's `instance_id`) through the existing
  `FederationRepository.upsert_mirror_account`. Accounts that would collide
  with a locally owned or otherwise-owned row, use an unrouted provider, go
  over the source's `max_accounts`, or carry an expired token are skipped
  with a reason. Rows previously pushed by that owner instance and absent
  from the push are removed, strictly scoped to that owner instance.
- The response returns Studio's local usage rollups for the accepted
  accounts, so the owner can see who used its accounts.
- Sender (Nate): `~/.agent-lb/federation-push.json` names targets and which
  owned accounts go to each. A background loop beside the mirror scheduler
  pushes every `federation_push_interval_seconds` (default 300) with
  per-target exponential backoff capped at 30 minutes, and stores returned
  usage under the target's name. A CLI (`python -m
  app.modules.federation.push status|once`) shows matches and pushes now.
- Settings: `federation_push_path`, `federation_push_interval_seconds`,
  `federation_push_sources_path`, `federation_tailscale_bin`.

## Impact

- Affected specs: `instance-federation`.
- Affected code: `app/modules/federation/` (schemas, api, repository, new
  `push.py` sender and `push_receiver.py` receiver), `app/core/config/settings.py`,
  `app/main.py` lifespan, tests.
- No database migration: pushed rows reuse the mirrored-account shape
  (`accounts.owner_instance`, placeholder refresh token) and returned usage
  reuses `federation_usage_daily`.
- The pull mirror (`FederationMirrorScheduler`, `/api/federation/mirror`,
  `federation_peer_url`) is unchanged. Pushed rows are not locally owned, so
  this LB's own `/mirror` never re-exports them and `AuthManager` never
  refreshes them.
- Money path (AGENTS.md rule 8): account credentials and custody, auth.
