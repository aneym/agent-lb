# Tasks

## 1. Spec

- [x] Create OpenSpec change with proposal and spec delta.

## 2. Implementation

- [x] Forward `response.output_item.*` events verbatim on the Codex-native
      surface (`enforce_openai_sdk_contract=False`) in
      `_normalize_public_stream_payload`.
- [x] Forward `response.completed`/`response.incomplete` terminal envelopes
      verbatim on the Codex-native surface.
- [x] Keep OpenAI-SDK (`enforce_openai_sdk_contract=True`) normalization
      unchanged.

## 3. Verification

- [x] Unit: Codex-native stream forwards a `compaction` output item and its
      terminal envelope verbatim (no `response.failed`).
- [x] Unit: `/v1` SDK contract still strips the unsupported `compaction` item.
- [x] Integration: `/backend-api/codex/responses` route returns the compaction
      output item verbatim for a v2-style stream.
- [x] `pytest` proxy responses + contract + codex upstream suites green.
- [x] `ruff check` clean on changed files.
- [x] Live: restarted the launchd service to deploy the fix; confirmed healthy
      and real Codex compaction still works end-to-end (no regression).
- [~] Live v2-on-account: could not be force-triggered. `responses_compaction_v2`
  is a server-delivered managed feature (not CLI-settable; `--enable`
  rejects it), and the accounts routed during verification used v1
  (`/responses/compact`). Real v2 reproduction depends on routing to a
  v2-enabled account. The integration test drives the exact v2 wire shape
  through the real `/backend-api/codex/responses` route instead.
