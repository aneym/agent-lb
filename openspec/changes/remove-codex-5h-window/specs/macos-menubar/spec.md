# macos-menubar

## ADDED Requirements

### Requirement: Codex scope renders weekly-only windows

When the provider scope is Codex (openai), the menu-bar client MUST NOT
render any 5-hour window UI: the pool section shows only the WEEKLY LIMIT
card, and codex account rows omit the 5H window cell and drive their leading
ring gauge from the weekly remaining percent. The All and Claude scopes MUST
keep both window cards, with the 5-hour aggregate reflecting only accounts
that still have a 5-hour window.

#### Scenario: Codex pool section

- **WHEN** the operator selects the Codex provider scope
- **THEN** the pool section renders a single WEEKLY LIMIT card and no
  5-HOUR LIMIT card

#### Scenario: Codex account row

- **GIVEN** an openai account in the accounts list
- **WHEN** its row renders in any scope
- **THEN** the row shows only the WK window cell and its ring gauge reflects
  the weekly remaining percent

#### Scenario: Claude scope unchanged

- **WHEN** the operator selects the Claude provider scope
- **THEN** the pool section renders both the 5-HOUR LIMIT and WEEKLY LIMIT
  cards
