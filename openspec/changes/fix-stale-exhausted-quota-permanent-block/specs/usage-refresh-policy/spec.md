## ADDED Requirements

### Requirement: Exhausted usage windows persist a bounded reset horizon

Background usage refresh MUST persist a bounded, self-expiring `reset_at` for any
exhausted window (`used_percent >= 100`) whose upstream payload omitted both
`reset_at` and `reset_after_seconds`, rather than storing `reset_at = None`.
The synthesized horizon MUST be `now + limit_window_seconds` when the upstream
window length is known and positive, otherwise `now + 3600` seconds. This applies
to Anthropic additional-quota windows and to the `primary` usage window.
Non-exhausted windows (`used_percent < 100`) MUST keep `reset_at = None` when
upstream provides no reset. The `secondary` and `monthly` usage windows are not
subject to this synthesis and keep their upstream-derived reset semantics.

#### Scenario: Exhausted additional-quota window with no upstream reset synthesizes a bounded reset

- **GIVEN** an upstream additional-rate-limit payload whose primary window reports
  `used_percent = 100`
- **AND** the payload omits both `reset_at` and `reset_after_seconds`
- **AND** the window reports a known `limit_window_seconds`
- **WHEN** the usage updater merges and persists the additional-quota window
- **THEN** the stored `reset_at` equals `now + limit_window_seconds`

#### Scenario: Exhausted window without a known window length uses the default horizon

- **GIVEN** an exhausted window (`used_percent = 100`) with no `reset_at`,
  `reset_after_seconds`, or usable `limit_window_seconds`
- **WHEN** the usage updater persists the window
- **THEN** the stored `reset_at` equals `now + 3600` seconds

#### Scenario: Non-exhausted window with no upstream reset stays unbounded

- **GIVEN** a window reporting `used_percent < 100` with no `reset_at` or
  `reset_after_seconds`
- **WHEN** the usage updater persists the window
- **THEN** the stored `reset_at` remains `None`
