## ADDED Requirements

### Requirement: Kimi tool results reference available calls
The Kimi provider boundary SHALL preserve valid tool use IDs and matching results. It SHALL assign deterministic nonempty IDs to malformed tool uses. It SHALL convert empty, duplicate, or unmatched tool result references to ordinary content while retaining text and image result content. It SHALL NOT send those results as tool results or change other providers' request bodies.

#### Scenario: Empty result reference
- **WHEN** a Kimi request includes a tool result with an empty tool_use_id
- **THEN** the provider forwards its content as text without a tool result reference

#### Scenario: Truncated tool history
- **WHEN** a Kimi request includes a result whose tool use is absent
- **THEN** the provider retains the result content without sending an unmatched tool result

#### Scenario: Valid parallel calls
- **WHEN** assistant tool uses have distinct nonempty IDs and matching user results
- **THEN** the provider preserves those IDs and results unchanged
