## ADDED Requirements

### Requirement: Status icon shows a Fable ring while a Claude app is focused

While the frontmost macOS application has a bundle identifier prefixed
`com.anthropic.`, the menubar status icon MUST widen with an additional
circle showing pool-level Fable remaining percent: a ring arc over the dim
track plus a centered `F` glyph. When the frontmost application is not a
Claude app, the icon MUST render at its original single-cell width with no
Fable circle. Pool-level Fable remaining percent MUST be the mean
Fable-scoped weekly remaining percent across routable Anthropic accounts
that report the `anthropic_fable_scoped_weekly` window — keeping exhausted
but routable accounts in the mean and excluding paused, disconnected, and
subscription-canceled accounts. If no routable account reports scoped data,
the Fable circle MUST render its track without an arc.

#### Scenario: Claude gains focus

- **GIVEN** routable Anthropic accounts reporting Fable-scoped weekly usage
- **WHEN** an application with bundle id `com.anthropic.claudefordesktop`
  becomes frontmost
- **THEN** the status icon shows the additional Fable circle with an arc
  proportional to the pool mean remaining percent

#### Scenario: Focus moves to a non-Claude app

- **GIVEN** the Fable circle is visible
- **WHEN** a non-Anthropic application becomes frontmost
- **THEN** the status icon returns to its original width without the Fable
  circle

#### Scenario: No scoped Fable data in the pool

- **GIVEN** no routable Anthropic account reports the Fable-scoped window
- **WHEN** a Claude app is frontmost
- **THEN** the Fable circle renders track-only (no arc)
