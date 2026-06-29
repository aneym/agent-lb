# Tasks

- [x] Add `_is_retryable_shim_connect_error` to classify connection-level
      failures (connection refused/reset) as retryable, excluding HTTP responses
      and read timeouts.
- [x] Retry retryable connection failures in the shim `_forward` loop with
      bounded exponential backoff before returning `502`; stop on parent exit.
- [x] Make retry count and backoff tunable via environment variables.
- [x] Add a unit test covering the retry classification (refused/reset retried;
      HTTPError and read timeout not retried).
- [x] Byte-compile the launcher and run the launcher unit tests.
- [x] Validate OpenSpec change locally (`npx --yes @fission-ai/openspec@latest validate --specs`).
