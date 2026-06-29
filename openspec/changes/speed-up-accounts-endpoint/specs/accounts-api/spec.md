# Accounts API

## ADDED Requirements

### Requirement: Accounts list defers request-usage rollup by default

`GET /api/accounts` SHALL omit the per-account `requestUsage` token/cost rollup by
default (returning `requestUsage = null`) so that latency-sensitive callers (the `cc`
launcher banner and the macOS menu bar) are not blocked by the request-usage
aggregation. Callers that need the rollup SHALL request it explicitly with the
`fresh=1` query parameter. The deferred aggregation, when requested, SHALL be
short-TTL cached so repeated requests within the TTL do not re-run the scan.

#### Scenario: Default response omits request-usage

- **WHEN** a client requests `GET /api/accounts` without `fresh=1`
- **THEN** each account's `requestUsage` field is `null`
- **AND** the request-usage aggregation does not run

#### Scenario: Fresh response includes request-usage

- **WHEN** a client requests `GET /api/accounts?fresh=1`
- **THEN** each account's `requestUsage` reflects the per-account request count, token
  totals, and cost rollup
- **AND** repeated `fresh=1` requests within the cache TTL reuse the cached rollup
