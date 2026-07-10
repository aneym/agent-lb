# Add a circuit breaker for repeated identical compaction turns

## Why

On 2026-07-10 two headless `codex exec` lanes retried the same ~133k-token
remote-compaction turn every ~50 seconds for three hours (361 requests,
~1.6M billed output tokens), draining every OpenAI pool account. The upstream
turns all "succeeded" from the proxy's perspective — the client discarded the
result each time due to a response-shaping bug — so nothing on the server
noticed or resisted the loop. Compaction bugs are now fixed
(`cd835bb8`, `985e8b54`), but the failure class (client retries an identical
compact forever while the proxy happily bills it) needs a structural guard: a
healthy client never resends the identical compaction turn many times, because
a successful compact changes the conversation input.

## What Changes

- `/backend-api/codex/responses` admission: track a fingerprint (hash of
  model + input array) for requests whose input contains a
  `compaction_trigger` item, in a bounded in-memory sliding window.
- When the same fingerprint is seen more than a threshold count within the
  window, reject the request locally with HTTP 429, a `Retry-After` header,
  and an error body naming the loop — before any upstream account is consumed.
- Record an audit log event when the breaker opens so the loop is visible in
  the dashboard/audit trail.
- Threshold and window are module constants (no new settings surface):
  more than 5 identical compaction turns within 10 minutes opens the breaker.

## Impact

- Affected specs: `proxy-admission-control`
- Affected code: `app/modules/proxy/api.py` (or a small helper module under
  `app/modules/proxy/_service/`), integration tests.
- Turns any future compaction regression from a silent pool-drainer into an
  immediate, visible client error.
