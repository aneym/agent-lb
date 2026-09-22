## ADDED Requirements

### Requirement: Named member lifecycle

The system SHALL provide dashboard-protected member creation, editing, suspension, deletion, aggregate usage, and one-time API-key issuance. Deletion SHALL detach existing keys without revoking them. The dashboard SHALL expose these operations on the Team page.

#### Scenario: Suspend and reactivate a member

- **WHEN** a trusted operator suspends a member
- **THEN** all that member's keys SHALL receive HTTP 403 with error type `team_member_suspended` on protected proxy calls
- **AND** reactivating the member SHALL restore access subject to the member's caps and model restrictions

#### Scenario: Invalid limits and narrow screens

- **WHEN** an operator enters a nonpositive cap or fractional token cap
- **THEN** the form SHALL prevent submission rather than silently removing the cap
- **AND** the member table SHALL preserve readable columns with horizontal scrolling on narrow screens

### Requirement: Team trust boundary

When team mode is enabled and global API-key authentication is disabled, trusted clients SHALL remain keyless and untrusted clients SHALL require a valid `sk-clb-` key. Client trust SHALL use the existing trusted-proxy-aware resolved IP. Untrusted clients SHALL receive HTTP 403 on dashboard-protected routes even when dashboard authentication is disabled. Self-service key usage SHALL remain accessible with a valid key.

#### Scenario: An untrusted member arrives through a trusted reverse proxy

- **WHEN** a loopback reverse proxy forwards an untrusted client IP
- **THEN** missing or invalid proxy credentials SHALL receive HTTP 401
- **AND** a valid member key SHALL allow proxy admission but SHALL NOT grant dashboard access

### Requirement: Aggregate calendar caps

The system SHALL enforce member model restrictions and aggregate cost and token caps across all attached keys before API-key usage reservation. Windows SHALL start at UTC midnight, Monday midnight, or the first day of the month. Reaching a cap SHALL return HTTP 429 with `team_member_over_cap`, `X-Team-Window`, and an ISO8601 UTC `X-Team-Reset`. Cached aggregates SHALL expire after five seconds or on a calendar-window boundary, whichever comes first.

#### Scenario: The calendar window resets before cache expiry

- **WHEN** the UTC day changes while yesterday's over-cap aggregate is cached
- **THEN** the next request SHALL use today's usage and SHALL NOT be denied based on yesterday's total

### Requirement: Literal onboarding commands

Onboarding SHALL return zsh and PowerShell snippets for Claude Code and Codex using the configured public base URL or request origin and a literal `<key>` placeholder. URL text SHALL NOT execute shell substitutions or additional commands when pasted into the corresponding shell.

#### Scenario: A URL contains shell metacharacters

- **WHEN** a configured URL contains quotes, dollar signs, or backticks
- **THEN** both onboarding snippets SHALL encode the URL as a literal value
- **AND** the response SHALL NOT expose an issued plaintext key

### Requirement: Existing data compatibility

The team migration SHALL preserve existing API keys and settings, default team mode to disabled, and leave existing keys unattached. The migration graph SHALL have one head.

#### Scenario: Upgrade an existing database

- **WHEN** the team migration runs on a database containing a key and settings
- **THEN** the key SHALL remain active with no member attachment
- **AND** team mode SHALL remain disabled
