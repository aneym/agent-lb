## ADDED Requirements

### Requirement: Menubar vendor tabs cover every CLI-seat vendor
The menubar SHALL have scope tabs for Cursor, Devin and Muse. Each tab SHALL count that vendor's proxy accounts plus its seat accounts. Grok SHALL appear as a model inside the Cursor tab. The Muse tab SHALL NOT include Muse accounts in Other.

#### Scenario: Muse seat counted
- **WHEN** one Muse seat account exists
- **THEN** the Muse tab shows count 1 and the All tab includes it

### Requirement: Vendor tabs show the vendor's real windows
A CLI-seat vendor tab SHALL render cards from that vendor's pool `windows`, of the vendor's real kinds. Each card SHALL show remaining percent, the reset in ET, the source, and the usable and total seat counts. An estimate SHALL be marked "est", and an observed value SHALL carry "as of" with its time.

#### Scenario: Cursor tab
- **WHEN** the Cursor tab opens with vendor readings for both Cursor pools
- **THEN** it shows "Cursor models · Grok, Composer" and "Other models" monthly cards with the reset in ET and no 5-hour card

#### Scenario: Muse not reported
- **WHEN** the Muse pool has `quotaSource` `unavailable`
- **THEN** the tab shows "Not reported by Muse" and the 24 h run count, with no ring

### Requirement: Vendor tabs without a reading stay quiet
A vendor with no reading SHALL show one quiet line naming the reason, such as "Not reported by Muse", and its 24 h run count. It SHALL NOT show empty 5-HOUR or WEEKLY rings or "0 accts".

#### Scenario: Devin credential unavailable
- **WHEN** the Devin pool has `quotaSource` `unavailable` with reason `credential_unavailable`
- **THEN** the Devin tab shows one line naming that reason and no empty ring

### Requirement: Seat accounts render as account rows
Seat accounts SHALL render with the same row component as Claude and Codex accounts: leading ring on the primary window, display name under the privacy mask, plan chip, state presentation from `status`, window grid and trailing today value. They SHALL take part in search, sort and counts. Plain-text maker sentences SHALL be removed.

#### Scenario: Cursor account out of a pool
- **WHEN** `cursor-gmail` has `status` `rate_limited` until Oct 30 00:00 UTC
- **THEN** its row shows the rate-limited presentation with "Oct 29, 8:00 PM" ET and the masked name under privacy mode

### Requirement: Scoped activity strip names its scope and unknowns
On a vendor tab the strip SHALL show that provider's request-log totals plus `seatActivity` runs, tokens and estimated value for 7 days. It SHALL say "tokens not reported" or "value unknown" instead of 0. It SHALL show "all providers" only on the All tab and SHALL use at most two decimals for dollars.

#### Scenario: Muse strip
- **WHEN** the Muse tab shows 3 seat runs with unknown tokens and no cost
- **THEN** the strip reads 7 d, 3 runs, tokens not reported, and does not show "$0.0000" or "all providers"
