# Instance Federation

## ADDED Requirements

### Requirement: Single refresh authority per account

Each account MUST have exactly one owner instance at any time, and only the
owner instance SHALL execute OAuth token refreshes for that account. Non-owner
instances MUST NOT initiate a refresh for accounts they mirror, regardless of
token age or expiry.

#### Scenario: Non-owner never refreshes

- **GIVEN** an account owned by instance A and mirrored on instance B
- **AND** the mirrored access token on B is within its refresh margin
- **WHEN** B evaluates the account for proactive refresh
- **THEN** B does not call the OAuth token endpoint
- **AND** B pulls a fresh access token from A instead

### Requirement: Token mirroring from the owner

A non-owner instance MUST obtain access tokens for mirrored accounts only from
the owner instance over the authenticated instance API, and MUST record the
owner-reported token expiry. Mirrored refresh tokens MUST NOT be used to call
the provider's OAuth endpoint while the instance is not the owner.

#### Scenario: Mirror refreshes its copy via the owner

- **GIVEN** instance B mirrors an account owned by reachable instance A
- **WHEN** B's mirrored access token nears expiry
- **THEN** B requests current tokens from A and stores them
- **AND** the account remains routable on B without B contacting the provider's
  OAuth endpoint

#### Scenario: First mirror pull follows an empty routing attempt

- **GIVEN** instance B cached an empty eligible-account selection before its
  first mirror pull completed
- **WHEN** B imports a usable mirrored account from owner instance A
- **THEN** B MUST invalidate the stale selection cache
- **AND** the mirrored account MUST be eligible on the next routing attempt

### Requirement: Checkout transfers refresh authority atomically

An operator-initiated checkout MUST transfer refresh authority such that the
previous owner has durably stopped refreshing before the new owner performs its
first refresh. At no point SHALL two instances both consider themselves owner
of the same account. Checkin MUST return authority the same way and sync the
latest (rotated) refresh token back to the returning owner.

#### Scenario: Checkout before a flight

- **GIVEN** accounts owned by studio and a laptop with connectivity to studio
- **WHEN** the operator checks out selected accounts to the laptop
- **THEN** studio marks the accounts non-owned and stops refreshing them before
  the laptop assumes ownership
- **AND** the laptop subsequently refreshes them normally while offline from
  studio

#### Scenario: Checkin syncs rotated tokens back

- **GIVEN** accounts checked out to the laptop whose refresh tokens have
  rotated
- **WHEN** the operator checks them back in to studio
- **THEN** studio receives the current refresh and access tokens before
  resuming refresh authority
- **AND** the laptop stops refreshing them

#### Scenario: Interrupted checkout cannot double-own

- **GIVEN** a checkout that fails after the previous owner released authority
  but before the new owner confirmed it
- **WHEN** either instance evaluates the account for refresh
- **THEN** neither instance refreshes it until the transfer is completed or
  rolled back by the operator flow

### Requirement: Degraded mode excludes unusable mirrors

A non-owner instance MUST exclude an account from routing selection when its
mirrored access token has expired and the owner is unreachable, rather than
attempt a refresh or route with the expired token.

#### Scenario: Owner unreachable with stale mirror

- **GIVEN** instance B mirrors an account whose access token has expired
- **AND** owner instance A is unreachable
- **WHEN** B selects accounts for a request
- **THEN** the account is excluded from the routable pool on B
- **AND** the stored tokens are left unmodified

### Requirement: Single-instance deployments are unaffected

A deployment with one instance MUST behave exactly as before this change: the
sole instance owns every account, refreshes proactively, and no mirroring or
transfer machinery is required or exercised.

#### Scenario: Default deployment

- **GIVEN** a single agent-lb instance with existing accounts
- **WHEN** the instance starts after migration
- **THEN** every account is owned by that instance and refresh behavior is
  unchanged
