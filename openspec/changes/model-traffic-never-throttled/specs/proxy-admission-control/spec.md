## ADDED Requirements

### Requirement: Upload holds never apply to model API traffic

Upload throttle holds MUST pace only writes made inside a bulk transfer (`upload_throttle.bulk_transfer()`). A write outside a bulk transfer, which includes every model API request body and websocket frame, MUST go to the socket without waiting for the token bucket, whatever holds are on. Bytes already queued from a bulk write on the same connection keep their order. Upload admission MUST let any request that is not a bulk transfer through at once, so a model request is never queued for upload room and never fails with `upload_admission_rejected` or `upload_admission_timeout`. `agent-lb throttle status` MUST say the cap applies to bulk transfers only.

#### Scenario: A game-mode hold does not delay a model request

- **GIVEN** a `game-mode` hold at the slowest accepted rate (65 KB/s)
- **WHEN** two Claude Code `/v1/messages` requests with 400 KB bodies go through the proxy to a non-loopback upstream at the same time
- **THEN** both stream back 200 with the upstream's events
- **AND** together they finish in well under the 12 s the hold would need to pace them
- **AND** both request log rows are `success` with no error code

#### Scenario: Bulk transfers are still paced

- **GIVEN** a hold is on
- **WHEN** a write is made inside `bulk_transfer()`
- **THEN** it is released no faster than the hold rate
