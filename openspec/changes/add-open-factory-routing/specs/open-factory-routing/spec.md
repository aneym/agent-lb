## ADDED Requirements

### Requirement: The route menu lists only seats the host can run now
`route menu` SHALL list, per routing-table class, every chain seat whose model resolves to a
served, non-retired model, whose seat is not recorded down in fresh routing state, and whose
pool is not exhausted or otherwise refused by the class's pace and audit rules. Every chain
seat that is not listed SHALL appear under `excluded` with the reason. `route pick` SHALL
return the first seat `route menu` lists for the same class.

#### Scenario: Exhausted pool
- **WHEN** the pool behind a class's first chain seat is exhausted
- **THEN** that seat appears under `excluded` with reason `pool <id> exhausted`
- **AND** the first listed seat is the one `route pick` returns

#### Scenario: Pool facts for a decider
- **WHEN** `route menu --json` runs while `/api/accounts` is reachable
- **THEN** each pool carries its best active account's 5-hour headroom and the earliest 5-hour reset

### Requirement: Open Factory routing keeps the move with the host
`open-factory route` SHALL offer the decider only the seats `route menu` lists for the class.
It SHALL dispatch the decider's pick only when that pick is a listed id and is still listed in
a fresh menu fetched after the decision. On an abstain, a decider failure, an unknown id or a
failed re-check it SHALL fall back to `route pick`, and to the driver session when nothing is
routable. It SHALL append exactly one `of_decision` row per decision to the dispatch ledger,
naming the candidates, the pick or abstain reason, the re-check result, the fallback and the
dispatched seat.

#### Scenario: Pick outside the menu
- **WHEN** the decider returns an id that was not on the menu
- **THEN** the decision records validation `unknown_id` and dispatches the `route pick` seat

#### Scenario: Decider unavailable
- **WHEN** `jev` exits 3 (unavailable)
- **THEN** the decision records abstain `jev_unavailable` and dispatches the `route pick` seat
