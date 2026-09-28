# instance-federation: delta

## ADDED Requirements

### Requirement: The push receiver identifies callers by Tailscale identity

The federation router (the non-dashboard `/api/federation` router) SHALL
expose `POST /api/federation/push` taking a `FederationPushRequest`
(`instance_id`, `accounts: list[FederationMirrorAccount]`) and returning a
`FederationPushResponse` (`source`, `accepted`, `skipped`, `removed`,
`usage`). The endpoint MUST NOT depend on the dashboard session gate (team
mode's untrusted-client gate included), a proxy API key, or any bearer token.

The receiver MUST authorize a caller as follows, and answer `403` when any
step fails:

1. Load the sources file at `federation_push_sources_path` (default
   `~/.agent-lb/federation-push-sources.json`), shaped
   `{"sources": [{"name": str, "tailscale_logins": [str], "max_accounts": int}]}`
   (`max_accounts` defaults to 25). The file is re-read when its mtime
   changes. A missing or unparseable file turns the feature off: every caller
   gets `403`.
2. Resolve the client IP with `resolve_request_client_host` (trusted-proxy
   aware). An unresolvable IP or a loopback IP is refused.
3. Run `tailscale whois --json <ip>` with the binary from
   `federation_tailscale_bin`, or else the first of
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale`,
   `/usr/local/bin/tailscale`, `/opt/homebrew/bin/tailscale`, `tailscale` on
   `PATH`. Output is parsed from the first `{`, because the CLI can print a
   `Warning: client version ...` line first. The login is
   `UserProfile.LoginName`. A failed or unparseable whois is refused.
   Successful lookups are cached per IP for 60 seconds; failures are not
   cached.
4. Match the login case-insensitively against every source's
   `tailscale_logins`. No match is refused.

A `Tailscale-User-Login` (or any other identity) request header MUST NOT
influence the decision.

#### Scenario: A listed Tailscale login may push

- **GIVEN** a sources file listing source `nate` with login `nate@example.com`
- **AND** whois for the caller's tailnet IP returns `LoginName` `Nate@Example.com`
- **WHEN** the caller posts a valid push
- **THEN** the response is `200` with `source` = `nate`

#### Scenario: Callers the receiver cannot place are refused

- **GIVEN** a sources file listing source `nate`
- **WHEN** a push arrives from a loopback IP, from a request whose client IP
  cannot be resolved, from an IP whose whois fails, or from an IP whose login
  is not listed
- **THEN** the response is `403` and no account row changes

#### Scenario: No sources file means nobody may push

- **GIVEN** no file at `federation_push_sources_path`
- **WHEN** a caller whose whois login would otherwise match posts a push
- **THEN** the response is `403`

#### Scenario: Identity headers are ignored

- **GIVEN** whois for the caller's IP returns an unlisted login
- **WHEN** the caller sends `Tailscale-User-Login: nate@example.com`
- **THEN** the response is `403`

#### Scenario: The real client IP behind tailscale serve is used

- **GIVEN** `firewall_trust_proxy_headers` is on and `127.0.0.1/32` is a
  trusted proxy CIDR
- **WHEN** a request arrives from socket peer `127.0.0.1` with
  `X-Forwarded-For: <tailnet IP>`
- **THEN** whois runs for the tailnet IP, not for `127.0.0.1`
- **AND** without proxy trust the same request is refused as loopback

#### Scenario: Whois is cached per IP

- **WHEN** two pushes arrive from the same IP within 60 seconds
- **THEN** `tailscale whois` runs once

#### Scenario: Team mode and proxy keys do not gate the push

- **GIVEN** team mode is on and `api_key_auth_enabled` is on
- **WHEN** a listed caller posts a push with no API key and no dashboard session
- **THEN** the response is `200`

#### Scenario: Sources file edits apply without a restart

- **GIVEN** a caller whose login is not listed was refused
- **WHEN** the sources file is rewritten to list that login
- **THEN** the caller's next push is accepted

### Requirement: A push source is bound to one sender instance

The first accepted push from a source SHALL record that source's
`instance_id` in a state file next to the sources file
(`federation-push-state.json`, mode `0600`). A later push from the same
source with a different `instance_id` MUST be refused with `409` and change
nothing. The receiver MUST also refuse with `409`, before binding, an
`instance_id` equal to this LB's `local_instance_id`, an `instance_id` bound
to another source, or an `instance_id` that already owns account rows here
that no source bound (for example a pull peer's instance id). Rebinding is a
manual edit of the state file.

#### Scenario: First push binds the source

- **WHEN** source `nate` pushes with `instance_id` `nate-lb` for the first time
- **THEN** the state file records `nate` -> `nate-lb` with mode `0600`

#### Scenario: A second instance cannot take over a bound source

- **GIVEN** source `nate` is bound to `nate-lb`
- **WHEN** source `nate` pushes with `instance_id` `other-lb`
- **THEN** the response is `409` and no account row changes

#### Scenario: A push cannot claim another owner's instance id

- **GIVEN** account rows here mirrored from pull peer `laptop`
- **WHEN** an unbound source pushes with `instance_id` `laptop`, or any
  source pushes with this LB's own `local_instance_id`
- **THEN** the response is `409`, nothing is bound, and the `laptop` rows are
  unchanged

### Requirement: Pushed accounts become mirrored rows owned by the sender

For an authorized, bound push, the receiver SHALL upsert each pushed account
through `FederationRepository.upsert_mirror_account` with
`owner_instance_id` = `request.instance_id`, so the row is routable here,
carries no refresh token, and is never refreshed here. The receiver MUST
skip (not fail) an account, listing it in `skipped` with a reason:

- `conflict_owned_elsewhere`: a row with that id exists here and is owned by
  this instance (`owner_instance` null or local) or by any instance other
  than `request.instance_id`, or the upsert declined (open transfer);
- `unsupported_provider`: its provider is not one this LB routes
  (`list_provider_names()`);
- `expired_token`: its `expires_at_ms` is at or before now;
- `over_limit`: accepting it would exceed the source's `max_accounts`
  (counted in request order over accounts that pass the other checks).

The receiver SHALL remove rows whose `owner_instance` equals
`request.instance_id` and whose id is absent from the push (an empty
`accounts` list removes them all), list them in `removed`, and never touch
any other row. An account present in the push but skipped is not removed.
When anything was upserted or removed, the account selection cache MUST be
invalidated.

#### Scenario: New accounts are accepted as mirrored rows

- **WHEN** a bound source pushes two anthropic accounts with live tokens
- **THEN** both ids are in `accepted`
- **AND** each row has `owner_instance` = the sender's `instance_id`, the
  pushed access token (encrypted), and an empty refresh token

#### Scenario: A locally owned or foreign-owned id is not overwritten

- **GIVEN** account `a` owned by this LB and account `b` mirrored from `laptop`
- **WHEN** a source pushes accounts `a` and `b`
- **THEN** both are skipped with `conflict_owned_elsewhere` and their rows,
  tokens and owners are unchanged

#### Scenario: Unrouted providers, expired tokens and overflow are skipped

- **GIVEN** a source with `max_accounts` 2
- **WHEN** it pushes an account with provider `made-up`, an account with
  `expires_at_ms` in the past, and three live anthropic accounts
- **THEN** the first two are skipped with `unsupported_provider` and
  `expired_token`, two anthropic accounts are accepted, and the third is
  skipped with `over_limit`

#### Scenario: Accounts dropped by the owner are removed, scoped to that owner

- **GIVEN** a source previously pushed accounts `x` and `y`, and account `z`
  is mirrored from `laptop`
- **WHEN** the source pushes only `x`
- **THEN** `y` is in `removed` and its row is gone, `x` is updated, and `z`
  is untouched
- **AND** a later push with an empty list removes `x` too

#### Scenario: Changes invalidate the selection cache

- **WHEN** a push upserts or removes a row
- **THEN** the account selection cache generation advances

### Requirement: The push response reports usage and never carries tokens

The response `usage` SHALL be this LB's local usage rollups
(`list_local_usage_rollups` over the last `federation_usage_window_days`)
filtered to exactly the accepted account ids. No access or refresh token may
appear in the response body or in any log line the receiver writes.

#### Scenario: Usage is filtered to accepted accounts

- **GIVEN** local request logs for pushed account `x` and for a locally owned
  account `o`
- **WHEN** the source pushes `x`
- **THEN** `usage` contains rollups for `x` only

#### Scenario: Tokens do not leak from the receiver

- **WHEN** a push is accepted, skipped or refused
- **THEN** neither the pushed access tokens nor any stored refresh token
  appear in the response body or the captured logs

### Requirement: Pushed rows stay out of this LB's own mirror export

Rows received by push MUST NOT be exported by this LB's
`GET /api/federation/mirror`, which keeps exporting only locally owned
accounts, and MUST NOT be refreshed by this LB. The pull mirror is unchanged.

#### Scenario: The mirror export skips pushed rows

- **GIVEN** a locally owned account and an accepted pushed account
- **WHEN** a pull peer calls `GET /api/federation/mirror`
- **THEN** only the locally owned account is returned

### Requirement: The push sender pushes chosen owned accounts to configured targets

The sender SHALL read `federation_push_path` (default
`~/.agent-lb/federation-push.json`) every cycle, shaped
`{"targets": [{"name": str, "url": str, "accounts": [str]}]}`. Each
`accounts` entry matches an owned account by email (case-insensitive) or by
account id. Only accounts in this instance's owned-account export (the set
`build_mirror_response` returns) are ever sent; entries that match nothing
are reported as unmatched. A push is `POST <url>/api/federation/push` with
`instance_id` = `local_instance_id`. A missing file means the loop pushes
nothing. A refresh token is never sent.

A background loop, started in the app lifespan beside the federation mirror
scheduler, pushes to every target every `federation_push_interval_seconds`
(default 300). After `n` consecutive failures a target is not tried again
until `min(1800, interval * 2^(n-1))` seconds after the failure; one success
resets it; other targets keep their own schedule. On success the returned
`usage` is stored through `accept_usage_report` with the target's `name` as
`instance_id`, and the last result per target is kept in memory.

#### Scenario: Only owned, matched accounts are sent

- **GIVEN** owned accounts `a@example.com` and `b`, and a mirrored row
  `m@example.com` owned by another instance
- **WHEN** a target lists `A@EXAMPLE.COM`, the id of `b`, `m@example.com`
  and `nobody@example.com`
- **THEN** the push carries `a` and `b` only, with their access tokens
- **AND** `m@example.com` and `nobody@example.com` are unmatched
- **AND** the serialized request contains no refresh token

#### Scenario: No config file, no pushes

- **GIVEN** no file at `federation_push_path`
- **WHEN** a cycle runs
- **THEN** no request is sent

#### Scenario: A failing target backs off without holding others

- **GIVEN** target `t1` fails every push and target `t2` succeeds
- **WHEN** cycles run every interval
- **THEN** `t1` is retried at `interval`, `2*interval`, `4*interval` ...
  after each failure, capped at 1800 seconds, while `t2` is pushed every cycle

#### Scenario: Returned usage is stored under the target name

- **WHEN** a push to target `alex-studio` succeeds with usage rollups
- **THEN** those rollups are stored in `federation_usage_daily` under
  instance `alex-studio`

### Requirement: The push sender has an operator CLI that never prints tokens

`python -m app.modules.federation.push status` SHALL print, per target, its
URL, matched accounts (email, provider, account id) and unmatched entries,
and exit `0`. `python -m app.modules.federation.push once` SHALL push every
target immediately, ignoring backoff, print accepted, skipped (with reason)
and removed per target, and exit `1` if any target failed. No output may
contain an access or refresh token.

#### Scenario: Status shows matches without tokens

- **WHEN** an operator runs `status`
- **THEN** each target's URL, matched emails, providers and ids, and
  unmatched entries are printed, the exit code is `0`, and no token appears

#### Scenario: Once reports per-target results and failures

- **GIVEN** one target that accepts and one that fails
- **WHEN** an operator runs `once`
- **THEN** the accepted, skipped and removed ids of the first are printed,
  the failure of the second is printed, the exit code is `1`, and no token
  appears
