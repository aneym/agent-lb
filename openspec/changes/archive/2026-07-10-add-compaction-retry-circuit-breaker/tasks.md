## Tasks

- [x] Implement the compaction fingerprint tracker (bounded, sliding-window)
      and wire it into `/backend-api/codex/responses` admission before account
      selection.
- [x] Reject over-threshold fingerprints with 429 + `Retry-After` and an
      explanatory error body; record an audit event once per window per
      fingerprint.
- [x] Integration tests: sixth identical compact 429s with no upstream call;
      distinct inputs unaffected; window expiry closes the breaker;
      non-compaction requests never fingerprinted.
- [x] Validation gates (pytest suites, ruff format/check, OpenSpec strict).
- [x] Live verification on the deployed proxy: replay an identical compaction
      probe 6× and observe the 429 + audit event.
