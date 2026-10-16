## ADDED Requirements

### Requirement: Dashboard identifies each CLI-seat vendor
The dashboard SHALL treat `muse` as a provider with its own mark and label. It SHALL label seats by their `vendor` (Cursor, Devin, Muse) and SHALL use the Grok mark for Grok models. An unknown provider SHALL get a neutral mark, never the Anthropic glyph.

#### Scenario: Muse seat row
- **WHEN** the Accounts table lists `muse-main`
- **THEN** the row shows the Muse mark and "Muse", not "Cursor" or the Anthropic glyph

### Requirement: Seat accounts use the account row with real readings
Seat account rows SHALL use the same Accounts table row as Claude and Codex accounts: state from `status`, a limit meter for each quota window of the vendor's real kind with reset in ET, and the today column from `today`. A missing reading SHALL show its reason, such as "Not reported by Muse", and never "No reading" in a 5-hour slot the vendor does not have. Rows SHALL NOT open a placeholder toast.

#### Scenario: Devin row
- **WHEN** `devin-main` has daily used 20 and weekly used 55
- **THEN** the row shows a Daily meter at 80 percent remaining and a Weekly meter at 45 percent remaining with their resets in ET

### Requirement: Pools and usage views include CLI-seat vendors
Pool rows, routing pool bars and the Usage page SHALL include Cursor pools, Devin and Muse with their `windows`. They SHALL show the monthly or unavailable state instead of "No weekly cap", and SHALL offer the seat vendors as provider filters with `seatActivity` counts.

#### Scenario: Usage page filtered to Muse
- **WHEN** the Usage page provider filter is Muse
- **THEN** it shows Muse request-log totals and Muse seat runs as separate figures
