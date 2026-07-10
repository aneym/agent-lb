# Fix direct-path remote-compaction turns misclassified as OpenAI-SDK requests

## Why

Codex remote-compaction v2 turns on the direct HTTP path
(`/backend-api/codex/responses`, `transport=http`) fail with
`invalid_output_item` ("Responses stream produced unsupported output items")
whenever the request omits a top-level `instructions` field.
`_is_openai_sdk_request` → `_has_openai_responses_shape` treats
`input`-without-`instructions` as an OpenAI-SDK-shaped request, which turns on
the public-contract normalizer; that normalizer's output-item whitelist has no
`compaction` type, so it drops the compaction item and aborts the stream. The
Codex CLI (`codex_exec` 0.144.0) then retries the identical compact turn
indefinitely — observed live on 2026-07-10 as 361 retries / ~1.63M billed
output tokens across two wedged lanes, exhausting all OpenAI pool accounts.

The June-29 `fix-codex-remote-compaction-v2` change missed this because every
one of its fixtures set a top-level `instructions` key, which keeps the
SDK-shape heuristic off. Confirmed live: an otherwise-identical probe passes
with `instructions` present and fails with `invalid_output_item` without it.

## What Changes

- `app/modules/proxy/api.py` `responses()` route: force
  `enforce_openai_sdk_contract` to false whenever the raw payload's `input`
  contains a `compaction_trigger` item (reusing
  `request_input_contains_compaction_trigger`). No OpenAI-SDK caller sends
  `compaction_trigger` items, so the override cannot misroute legitimate SDK
  traffic.
- Regression tests at the failing surface covering a compaction turn WITHOUT
  top-level `instructions`.

## Impact

- Affected specs: `responses-api-compat`
- Affected code: `app/modules/proxy/api.py`,
  `tests/unit/test_proxy_api_responses_contract.py`
- Fixes the live codex compaction retry loop for headless `codex exec` lanes.
