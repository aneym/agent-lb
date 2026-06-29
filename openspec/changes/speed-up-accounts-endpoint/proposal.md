# Speed Up Accounts Endpoint

## Why

`GET /api/accounts` took ~2.9s on a populated database, dominated by a per-account
request-usage aggregation (a `row_number()` dedup window over the full
`request_logs` history, plus full-table scans in the additional-usage loop). The
`cc` launcher and the macOS menu bar call this endpoint on every startup just to
render quota banners and account rows, and neither consumes the request-usage
token/cost data. The aggregation made Claude Code startup ~73% slower than it
needed to be, and the cost grows with request history.

## What Changes

- `GET /api/accounts` defers the expensive per-account request-usage aggregation by
  default; the response omits `requestUsage` (null) unless the caller passes
  `?fresh=1`. The default path returns in <150ms.
- The dashboard, which renders per-account token/cost columns, requests `?fresh=1`.
- The request-usage aggregation is short-TTL cached so repeated `?fresh=1` polls do
  not re-run the scan.
- The additional-usage per-quota-key queries are scoped to the live account ids so
  they stop scanning history for accounts that no longer exist.
- The `cc` launcher caps the banner-enrichment `/api/accounts` call so it is never on
  the startup critical path even if the endpoint is slow.

## Non-Goals

- No change to how usage/quota pressure is computed or refreshed (still
  scheduler-backed); only the request-usage token/cost rollup is deferred.
- No change to the `requestUsage` schema or its values when present.
- No change to proxy routing or account selection.
