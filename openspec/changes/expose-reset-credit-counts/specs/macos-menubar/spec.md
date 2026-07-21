## ADDED Requirements

### Requirement: Account rows show banked reset credits

The menu bar popover MUST show a compact banked-resets chip on OpenAI account rows when `resetCreditsAvailable` is present, MUST render a zero count in a visually de-emphasized state, and MUST show no chip when the field is absent (older servers) or the account belongs to another provider.

#### Scenario: Codex account with banked credits

- **WHEN** an OpenAI account reports a positive `resetCreditsAvailable`
- **THEN** its row shows a chip with that count

#### Scenario: Count unavailable

- **WHEN** the server omits `resetCreditsAvailable`
- **THEN** the row renders exactly as before, with no chip
