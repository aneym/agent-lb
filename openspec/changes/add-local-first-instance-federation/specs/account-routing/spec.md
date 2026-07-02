## ADDED Requirements

### Requirement: Launcher prefers a healthy local instance

The Claude launcher MUST attempt LB endpoints in preference order: a local
instance (when configured), then the configured remote LB, then plain-claude
fallback. The first endpoint that passes the health probe and claims a session
route serves the whole session. Existing sessions MUST keep their originally
claimed endpoint and route; preference changes apply only to new sessions.

#### Scenario: Local instance healthy

- **GIVEN** a local agent-lb is configured and healthy
- **WHEN** a new session launches
- **THEN** the launcher routes the session through the local instance without
  probing the remote LB

#### Scenario: Local instance absent or unhealthy

- **GIVEN** no local instance is configured, or its health probe fails
- **WHEN** a new session launches
- **THEN** the launcher falls through to the configured remote LB, and then to
  plain claude, preserving current behavior
