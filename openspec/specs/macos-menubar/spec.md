# macOS Menu-bar Specification

## Purpose

Define the operator-visible behavior and state semantics of the native macOS
menu-bar client.

## Requirements

### Requirement: Menu-bar section errors represent completed fetch failures

The macOS menu-bar client MUST show a section retry state only when the most
recent completed fetch for that section failed. Cancelling an in-flight fetch
because the popover closed or restarted MUST NOT create or clear the section's
error state. A successful foreground or silent background fetch MUST clear an
older error for the same section.

#### Scenario: Closing the popover cancels healthy in-flight fetches

- **WHEN** pool, accounts, or recent fetches are cancelled by popover lifecycle
- **THEN** the client does not add a visible error for those sections
- **AND** cached section data can render without a misleading retry row

#### Scenario: Silent refresh repairs a stale error

- **GIVEN** a section has a recorded error from a genuine completed failure
- **WHEN** its closed-state silent fetch later succeeds
- **THEN** the client clears that section error

#### Scenario: Genuine failure with stale data remains visible

- **WHEN** a non-cancellation section fetch fails after stale data already exists
- **THEN** the client preserves the data and shows the retry affordance

### Requirement: Menu-bar read deadlines account for service locality

The macOS menu-bar client MUST use a short bounded health-probe deadline for
loopback services, a longer bounded dashboard-read deadline for healthy local
database work, and bounded remote deadlines that cover cold remote connection
setup. A healthy health or dashboard response that completes within its
applicable deadline MUST update service or section state without marking the
service unreachable or adding a retry state.

#### Scenario: Loopback service fails fast

- **WHEN** the configured service host is `127.0.0.1`, `localhost`, or `::1`
- **THEN** the client uses the 3-second request / 5-second resource health-probe
  envelope

#### Scenario: Healthy cold loopback dashboard read exceeds the health deadline

- **GIVEN** the configured service host is `127.0.0.1`, `localhost`, or `::1`
- **WHEN** a healthy dashboard response takes longer than the loopback health
  deadline but completes within the 15-second request / 20-second resource
  dashboard deadline
- **THEN** the client accepts the response and does not show a retry state for
  that fetch

#### Scenario: Cold Tailnet health response exceeds the local deadline

- **GIVEN** the configured service host is remote
- **WHEN** a healthy health response takes longer than the local request
  deadline but completes within the remote deadline
- **THEN** the client accepts the response and does not classify the service as
  unreachable

#### Scenario: Tailnet dashboard response exceeds the local deadline

- **GIVEN** the configured service host is remote
- **WHEN** a healthy dashboard response takes longer than the local request
  deadline but completes within the remote deadline
- **THEN** the client accepts the response and does not show a retry state for
  that fetch

#### Scenario: Remote health probe exceeds its bounded deadline

- **WHEN** a remote health probe does not complete within the remote deadline
- **THEN** the client records a genuine health failure and classifies the remote
  service as unreachable

#### Scenario: Remote dashboard read exceeds its bounded deadline

- **WHEN** a remote dashboard read does not complete within the remote deadline
- **THEN** the client records a genuine completed timeout failure for that
  section

### Requirement: Menu-bar reset display distinguishes a full window from a pending recovery

The macOS menu-bar client MUST render `Full · no reset needed` instead of a
reset countdown when a displayed pool window is known full. A window is known
full when positive-capacity credit telemetry has remaining credits greater than
or equal to capacity, or, when those credits are unavailable, reported
remaining percent is at least 100. It MUST NOT infer full from a rounded
percentage, zero capacity, or missing telemetry.

#### Scenario: Full five-hour pool does not promise zero recovery

- **GIVEN** a five-hour window reports `500 / 500` credits and a future reset timestamp
- **WHEN** the menu-bar pool card renders
- **THEN** it shows `Full · no reset needed`
- **AND** it does not show a countdown or `+0 cr`

#### Scenario: Scoped recovery ignores a full earlier account

- **GIVEN** a full account resets before a depleted account in the same scoped pool
- **WHEN** the menu-bar computes the next recovery and its hover schedule
- **THEN** the depleted account determines the next recovery
- **AND** the full account is omitted from that schedule

#### Scenario: Unknown telemetry remains unknown

- **GIVEN** an account has a future reset timestamp but missing credit and percent telemetry
- **WHEN** the menu-bar computes a scoped pool
- **THEN** it does not infer that the pool or account is full
- **AND** the account can still determine the displayed unknown reset boundary

#### Scenario: Fractional recovery is not displayed as zero

- **WHEN** a known positive recovery is less than one credit
- **THEN** the menu-bar renders `+<1 cr` rather than `+0 cr`

#### Scenario: Full account row omits the countdown

- **GIVEN** an account has a known-full five-hour window and a depleted weekly window
- **WHEN** its row renders
- **THEN** the five-hour window displays its remaining percent without a countdown
- **AND** the weekly window retains its own reset countdown

### Requirement: All accounts default to remaining usage order

The macOS menu-bar All view MUST default to ascending remaining usage percentage,
independently of the provider-scoped sort preference. The comparison MUST use
weekly (secondary) remaining percentage exclusively, without monthly or
five-hour fallbacks. Unknown weekly values MUST sort last and known zero MUST remain
zero. Equal values MUST sort by case-insensitive display name then account ID.
Explicit alternative sort selections MUST remain available and persist separately
for All and provider-scoped views.

#### Scenario: Mixed providers are ordered by remaining weekly allowance

- **GIVEN** accounts from different providers have weekly remaining percentages of 40, 0 and 9
- **WHEN** the All view opens with no saved All-view sort preference
- **THEN** their order is 0, 9, 40 regardless of reset times or the provider-view sort preference

#### Scenario: Unknown usage is not treated as exhausted

- **GIVEN** one account has no known remaining percentage and another has zero weekly remaining
- **WHEN** sorting by remaining usage
- **THEN** the zero-remaining account precedes the unknown account

#### Scenario: Weekly telemetry takes precedence over shorter windows

- **GIVEN** an account has 10 percent weekly remaining and 100 percent five-hour remaining
- **WHEN** sorting by remaining usage
- **THEN** its comparison value is 10 percent
- **AND** an account without weekly telemetry sorts last even if monthly or five-hour telemetry is available

#### Scenario: Provider sort preference survives All-view selection

- **GIVEN** a provider-scoped view uses reset-soonest ordering
- **WHEN** the operator changes the All-view sort selection
- **THEN** the provider-scoped view keeps reset-soonest ordering
