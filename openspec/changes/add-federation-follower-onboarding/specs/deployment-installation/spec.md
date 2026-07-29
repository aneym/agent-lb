# deployment-installation — delta

## ADDED Requirements

### Requirement: Follower onboarding installer

The repository SHALL provide `scripts/install-federation-follower.sh` which
configures the local launchd service as a federation follower of a given
peer. It MUST accept the peer URL and local instance id as flags (instance id
defaulting to a hostname-derived value) and the federation token ONLY via
environment variable or a file path flag — never as a command-line argument
value. It MUST inject exactly the federation environment keys
(`AGENT_LB_LOCAL_INSTANCE_ID`, `AGENT_LB_FEDERATION_TOKEN`,
`AGENT_LB_FEDERATION_PEER_URL`,
`AGENT_LB_FEDERATION_MIRROR_INTERVAL_SECONDS`) into the service environment
while preserving all unrelated operator-provided environment variables
(plist regeneration otherwise follows the existing service installer's
semantics), restart the service, and then
poll `GET /api/federation/status` until a mirror pull succeeds, failing with
a non-zero exit and the last mirror error after a bounded timeout. The script
MUST be idempotent, MUST support `--print` (show the resulting environment
with the token redacted, change nothing), and MUST support `--uninstall`
(remove only the federation keys and restart). If the launchd service is not
installed yet it MUST install it via the existing service installer.

#### Scenario: Fresh follower onboarding

- **GIVEN** a machine with the service installed and no federation
  environment, and a reachable owner peer
- **WHEN** the operator runs the script with a peer URL and the token in the
  environment
- **THEN** the service restarts with the four federation keys set
- **AND** the script exits `0` only after the status endpoint reports a
  successful mirror pull

#### Scenario: Unreachable peer fails visibly

- **GIVEN** a peer URL that is not reachable
- **WHEN** the operator runs the script
- **THEN** it exits non-zero within the bounded timeout and prints the last
  mirror error from the status endpoint

#### Scenario: Idempotent re-run

- **GIVEN** a machine already configured as a follower of peer P
- **WHEN** the operator re-runs the script with the same values
- **THEN** the environment is unchanged, the service is healthy, and the
  script exits `0`

#### Scenario: Uninstall removes only federation keys

- **GIVEN** a configured follower whose plist also carries unrelated
  environment keys
- **WHEN** the operator runs `--uninstall`
- **THEN** the federation keys are removed, unrelated keys survive, and the
  service restarts
