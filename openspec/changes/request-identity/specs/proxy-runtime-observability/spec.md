## ADDED Requirements

### Requirement: Request-log identity uses a validated person and the auth client-IP rule

For every new request log the runtime SHALL store `caller_user`,
`caller_user_source`, `caller_machine`, and `caller_machine_source` at write
time. A validated key with `member_id` resolves the member name by id using a
small cached lookup, without editing the team member or key data types; the
cache retains only the slugified handle or null (never the raw member name).
Email-shaped or empty member names resolve to `unknown`. A valid member handle
is the user with source `member`. Otherwise the user is the
configured owner with source `owner-machine` ONLY when the resolved machine is
in the configured owner-machine set; otherwise it is `unknown` with source
`unknown`. A key name and a tailnet login MUST NOT be treated as a person.
User handles MUST be at most 32 characters and contain no email address.

The machine IP MUST come from `resolve_connection_client_ip` using the same
settings as firewall and auth, without an independent forwarded-header parser.
A loopback IP yields `local` and the configured local machine (falling back to
`local`); a loopback connection with a valid `X-Agent-LB-Machine` handle yields
`claimed` and that handle. A claimed machine on the owner list attributes the
owner only if the local machine is also on the owner list; a claim cannot raise
a loopback caller above their attribution without the header. Valid claimed handles match
`^[a-z0-9][a-z0-9-]{0,47}$`; invalid claims do not override the local
identity. An IP in 100.64.0.0/10 or fd7a:115c:a1e0::/48 yields `tailnet`
and its node-cache handle after aliases, or `tailnet-unknown` on a miss. A
Serve funnel request yields `funnel` and machine `public`; any other remote
IP yields `remote` and machine `remote` without recording that IP. Non-request
work yields `internal` for both sources. Machine handles MUST be at most 48
characters. Source values MUST distinguish `member`, `owner-machine`,
`internal`, `unknown` for users and `local`, `tailnet`, `funnel`, `remote`,
`claimed`, `internal` for machines.

`AGENT_LB_IDENTITY_ENABLED` (default true), `AGENT_LB_IDENTITY_OWNER`,
`AGENT_LB_IDENTITY_OWNER_MACHINES` (comma-separated),
`AGENT_LB_IDENTITY_LOCAL_MACHINE`, `AGENT_LB_IDENTITY_MACHINE_ALIASES`
(comma-separated `old=new` pairs), and `AGENT_LB_IDENTITY_TAILSCALE_BIN`
SHALL be flat environment settings. Instance-specific handles MUST NOT be
committed to the public repository.

#### Scenario: Shared login does not identify the caller

- **GIVEN** a keyless request from a tailnet machine not on the owner list
- **WHEN** it is logged, even if a Serve login header is present
- **THEN** its user is `unknown`, source `unknown`, not the login or key name

#### Scenario: Member key is authoritative

- **WHEN** a validated member key is used from a machine not on the owner list
- **THEN** the log stores the member handle with source `member`

#### Scenario: Owner machine without a key

- **WHEN** a keyless request resolves to an owner-listed machine
- **THEN** the log stores the configured owner with source `owner-machine`

#### Scenario: Forged or ambiguous forwarding headers follow auth

- **WHEN** a loopback caller sends a forged or multi-valued X-Forwarded-For header
- **THEN** its identity uses exactly the client IP returned by the auth resolver
- **AND** no independent parsing of that header changes attribution

#### Scenario: Claimed machine is self-reported

- **WHEN** a loopback caller supplies a valid claimed handle such as `box-2`
- **THEN** the machine is `box-2` with source `claimed`
- **AND** if `box-2` is on the owner list, the user is the owner with source `owner-machine`
- **AND** a claimed handle not on that list leaves the user `unknown`
- **AND** an invalid handle is not stored; a remote or funnel caller cannot claim an owner machine

### Requirement: Tailnet node handles are cached without login data

A background job SHALL refresh tailnet IP-to-node handles every 60 seconds by
running the configured `tailscale status --json` binary with a five-second
timeout. It SHALL read only `TailscaleIPs` and the first label of `DNSName`
for Self and Peers; it MUST NOT read, retain, or log User/login fields. Failed
refreshes MUST preserve the last good cache, with at most one warning per
hour. Request handling MUST NOT run a subprocess or wait on cache refresh.

#### Scenario: Cache is unavailable

- **WHEN** a refresh fails or a valid tailnet address is missing from cache
- **THEN** requests continue and missing nodes log as `tailnet-unknown`
- **AND** previously cached entries survive a refresh failure

#### Scenario: Login fields are ignored

- **GIVEN** a status response containing invented node names and a User block
- **WHEN** the response is cached
- **THEN** only IPs and DNS first-label node handles enter the cache

### Requirement: Every request-log writer carries identity across transports

ASGI middleware alongside client-session middleware SHALL capture machine
identity for HTTP requests and WebSocket connections in a request contextvar;
validated key identity SHALL be added after validation. The request-log
repository SHALL accept an explicit identity argument, fall back to the
contextvar, and use `internal` when no request context exists. Proxy logging,
Anthropic, streaming, and WebSocket turns MUST persist the request's identity.
A WebSocket MUST capture identity for each turn, not only at connect. Quota and
limit warmups without requests MUST record `internal`, never an owner.

The origin of an HTTP bridge forward SHALL include its resolved identity in
the existing forward context. The owner SHALL accept that identity ONLY via
the authenticated bridge path; a plain client-supplied context MUST NOT
replace resolved identity. Forward reuse and cancellation MUST preserve the
same attribution and MUST NOT leak it to another request.

#### Scenario: Multiple WebSocket turns retain per-turn identity

- **WHEN** a WebSocket connection sends two separately logged turns
- **THEN** both log rows carry identity captured for their respective turns

#### Scenario: Bridge forwards without becoming local

- **WHEN** an authenticated bridge forwards, reuses, or cancels a remote request
- **THEN** its request log retains the origin identity rather than the owner instance's local identity
- **AND** a direct client cannot supply trusted forward identity

#### Scenario: Internal warmup is not an owner request

- **WHEN** a quota or limit warmup logs without a request context
- **THEN** both attribution sources are `internal`

### Requirement: Request logs and bounded usage reads expose attribution

The authenticated request-log API SHALL return all four attribution fields,
including null for historical rows. The existing authenticated dashboard usage
API SHALL provide a bounded-window summary grouped by caller user and machine
with request counts, input/output tokens and cost where existing rollups have
cost. Its exclusions and treatment of soft-deleted rows MUST match the
existing usage metrics. It MUST NOT expose email, tailnet login, or client IP.
Federated per-instance daily aggregates remain unchanged.

#### Scenario: Usage summary separates people and machines

- **GIVEN** logs in one window for `owner-a` on `box-1` and `owner-a` on `box-2`
- **WHEN** an authenticated dashboard client requests the user/machine summary
- **THEN** two groups show the matching counts, token totals, and available cost
- **AND** the existing usage exclusions and soft-delete rules are applied

#### Scenario: Historical row remains readable

- **WHEN** the request-log API returns a pre-migration row
- **THEN** all four attribution fields are null and the row remains readable
