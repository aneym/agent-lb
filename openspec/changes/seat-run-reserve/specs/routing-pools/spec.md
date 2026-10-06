## ADDED Requirements

### Requirement: seat run reserves capacity before it launches a vendor CLI
`seat run` with `--class` and no `--account` SHALL hold a `route reserve` lease for its seat before it writes the dispatch row or starts the vendor CLI, SHALL renew it while the CLI runs, and SHALL release it on exit with `ok`, `failed` or `cancelled`. It SHALL exit 4 on a capacity wait, 2 on a refusal and 1 when reservations are unavailable, launching nothing in each case.

#### Scenario: A second run waits while capacity is held
- **GIVEN** Cursor capacity 1 held by a running `seat run --vendor cursor --class mechanical`
- **WHEN** a second such run starts
- **THEN** it exits 4 with `ok: false`, an error starting `capacity: ` and an integer `wait`, and the vendor CLI is not started

#### Scenario: Release frees the slot
- **GIVEN** the holder finishes, fails or receives SIGTERM
- **WHEN** reservations are listed
- **THEN** its lease is no longer live and its outcome is `ok`, `failed` or `cancelled` respectively

#### Scenario: Runs without a class or with a pinned account stay unreserved
- **GIVEN** `seat run` without `--class`, or with `--account`
- **WHEN** it runs
- **THEN** it takes no lease and its dispatch row carries `reservation: null` and the reason

#### Scenario: A lost lease stops the run
- **GIVEN** a running `seat run --class` whose lease route reports gone (it expired or was ended elsewhere)
- **WHEN** the next heartbeat runs
- **THEN** the vendor CLI's process group is stopped and the run exits 1 with an error naming the lost reservation

#### Scenario: A signal never cuts the release short
- **GIVEN** SIGTERM or Ctrl-C arrives during the reserve, the run or the release
- **WHEN** the run ends
- **THEN** the lease is released (`cancelled` when the signal came before the release began) and the run exits 128 plus the signal
