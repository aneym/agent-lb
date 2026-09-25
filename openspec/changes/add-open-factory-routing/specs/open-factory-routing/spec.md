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

### Requirement: The class default wins while it fits

The first seat on a class's menu (the head of its routing-table chain) SHALL be the house default. When the decider picks another seat, `open-factory route` SHALL dispatch the default instead if the decider's fit for the default is at or above the policy's `default_min_fit`. The receipt SHALL record the decider's own pick, the default and the default's fit.

#### Scenario: Codex has room for implementation

- **WHEN** the implement menu is headed by `gpt-implementer` on a routable `openai-codex` pool and the decider picks `sonnet-implementer` while rating `gpt-implementer`'s fit at 0.8 or more
- **THEN** the seat is `gpt-implementer`, `kept_default` is true, and `decider_pick` is `sonnet-implementer.sonnet`

#### Scenario: The default does not fit

- **WHEN** the decider picks another menu seat and rates the default's fit below `default_min_fit`
- **THEN** the decider's pick is dispatched after the usual fresh-menu recheck

### Requirement: Artificial Analysis benchmarks are evidence, not routing

`open-factory aa-sync` SHALL fetch Artificial Analysis model data at most once per 24 hours unless `--force` is given. It SHALL write each fetch as a timestamped snapshot plus `latest.json`, and map AA slugs to seat aliases only through the checked-in `aa_map.json`, listing any other slug as unmapped. The API key SHALL be read from `ARTIFICIAL_ANALYSIS_API_KEY` only and SHALL never appear in output or snapshots. Evidence older than 8 days SHALL be withheld. The command SHALL NOT change any routing decision.

#### Scenario: A second sync the same day

- **WHEN** a snapshot under 24 hours old exists and `aa-sync` runs without `--force`
- **THEN** no request is made and the result reports `skipped`

#### Scenario: No key

- **WHEN** `ARTIFICIAL_ANALYSIS_API_KEY` is unset and a fetch is due
- **THEN** the command fails with "ARTIFICIAL_ANALYSIS_API_KEY is not set" before any request

#### Scenario: Dry run

- **WHEN** `aa-sync --dry-run` runs
- **THEN** it summarizes the saved fixture without a key and writes nothing
