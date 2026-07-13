## ADDED Requirements

### Requirement: OAuth on a follower fails visibly toward the owner

A federation follower instance (one with `federation_peer_url` configured)
MUST NOT complete a new dashboard-originated OAuth login locally, because
mirrored accounts never carry refresh tokens and a locally-persisted
credential would be orphaned. Every dashboard OAuth entry point
(`POST /api/oauth/start`, `/complete`, `/manual-callback`) MUST reject the
request with an actionable error naming the owner instance's URL instead of
starting or advancing the browser/device flow.

#### Scenario: Dashboard OAuth start on a follower is rejected

- **GIVEN** an instance with `federation_peer_url` set to the owner's URL
- **WHEN** a client calls `POST /api/oauth/start`
- **THEN** the response is `409` with error code `oauth_owner_required`
- **AND** the error message contains the configured owner URL
- **AND** no OAuth flow is left pending in the flow store

#### Scenario: Dashboard OAuth complete/manual-callback on a follower are rejected

- **GIVEN** an instance with `federation_peer_url` set to the owner's URL
- **WHEN** a client calls `POST /api/oauth/complete` or
  `POST /api/oauth/manual-callback`
- **THEN** the response is `409` with error code `oauth_owner_required`,
  regardless of whether a flow exists in the store

#### Scenario: Owner and standalone instances are unaffected

- **GIVEN** an instance with no `federation_peer_url` configured
- **WHEN** a client calls `POST /api/oauth/start`
- **THEN** the existing browser/device OAuth flow proceeds exactly as before
  this change

### Requirement: The CLI auth script targets the federation owner by default

`scripts/anthropic-auth.sh` MUST default to the configured federation
owner/peer URL when run on a follower instance, rather than always assuming
the local instance owns OAuth. An explicit `BASE_URL` environment variable
MUST still take precedence over the derived default.

#### Scenario: Script run on a follower without BASE_URL set

- **GIVEN** the local `com.aneyman.agent-lb` launchd service has
  `AGENT_LB_FEDERATION_PEER_URL` set in its environment
- **AND** `BASE_URL` is not set in the caller's shell
- **WHEN** `scripts/anthropic-auth.sh start` runs
- **THEN** the request targets the configured federation peer URL, not
  `http://127.0.0.1:2455`

#### Scenario: Explicit BASE_URL always wins

- **GIVEN** any instance, follower or owner
- **WHEN** `BASE_URL` is set in the caller's shell before running the script
- **THEN** the script uses that value unmodified
