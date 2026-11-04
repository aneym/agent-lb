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

### Requirement: Kimi history normalization preserves result meaning and ordering
The Kimi boundary SHALL place kept tool result blocks before ordinary content in user messages. It SHALL preserve orphan error status as an error text marker and render unsupported result parts as text. It SHALL pair repaired empty or duplicate call IDs with corresponding results in order, replace empty result content with a placeholder, and supply an error placeholder for missing historical results. Only a final assistant message with tool uses awaiting execution SHALL remain without results.

#### Scenario: Orphan precedes a valid result
- **WHEN** a user message contains an orphan result before a valid result
- **THEN** the valid result precedes the converted orphan content upstream

#### Scenario: Rich orphan error
- **WHEN** an orphan error contains document, tool reference, or search result parts
- **THEN** its text preserves the error marker and those parts

#### Scenario: Missing historical result
- **WHEN** an assistant tool use has no result before a later assistant message
- **THEN** the upstream history includes an error result placeholder for that call
