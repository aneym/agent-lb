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

#### Scenario: No model listing before the hold
- **GIVEN** a cold Cursor model cache
- **WHEN** `seat run --class` reserves, or waits for capacity
- **THEN** no vendor model listing runs before the reservation is live, and a run that waits lists nothing

#### Scenario: The reserved model is the model that runs
- **GIVEN** `seat run --vendor cursor --class verify --author-vendor xai --model grok-latest`, where Cursor's only verify rung runs Sonnet
- **WHEN** it reserves
- **THEN** route reserves nothing for Grok (`route reserve --model` picks only a rung that runs the caller's model), the run exits 2 and the vendor CLI is not started; a reservation whose model differs from the caller's is released `failed` and refused

#### Scenario: A stop waits for the whole process group
- **GIVEN** a vendor CLI whose descendant ignores SIGTERM
- **WHEN** the run is stopped
- **THEN** the run ends and releases only after the descendant is gone (SIGKILL after 10 s, or at once on a second stop signal)
- **AND** a process that outlives SIGKILL keeps the run waiting (SIGKILL resent each grace period) and the lease beating; nothing is released over a live group

#### Scenario: A family alias names its rung at the ladder's effort
- **GIVEN** `seat run --vendor cursor --class mechanical --model grok-latest`, whose ladder runs Grok low on Cursor
- **WHEN** it reserves
- **THEN** the grok-low rung is reserved and Grok low runs: a family alias or model id at another effort (`-low`, `-medium`, `-high`, `-xhigh`, `-max`) names the rung, and another family is refused with what the seat's rungs run

#### Scenario: Models listed under the hold settle the reservation
- **GIVEN** a cold vendor model cache
- **WHEN** route lists models under the hold
- **THEN** a stop ends the listing and releases the hold `cancelled`; the hold is renewed afterwards and stores the resolved model (a hold lost meanwhile runs nothing); a reviewer that waited on the listing must resolve too, or the worker's hold is released; a hold route gives up and cannot release ends the reserve with an error, never another rung's success
