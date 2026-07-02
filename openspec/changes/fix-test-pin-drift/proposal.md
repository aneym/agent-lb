# Fix pre-existing test-pin drift (7 failures)

## Why

Seven tests fail on a clean `main` checkout, blocking a green suite and
masking real regressions. Diagnosis showed no production bugs — every failure
is a stale or brittle test pin left behind by intentional shipped changes:

1. `test_proxy_utils.py::test_logged_error_json_response_emits_proxy_error_log`
   pinned the CodeQL-era behavior (`2992b86d`) of omitting error code/message
   from proxy error logs. Commit `ba538892` ("uncap local admission and
   improve diagnostics") intentionally restored `code=`/`message=` **with
   secret redaction** (`_redact_log_value`), and the main
   `proxy-runtime-observability` spec already requires code+message logging —
   the pin contradicted the spec.
2. Three websocket/bridge replay tests
   (`test_pop_replayable_created_without_visible_output_request_state`,
   `test_process_upstream_websocket_text_rewrites_replayed_created_only_response_id`,
   `test_retry_http_bridge_precreated_request_replays_created_without_visible_output`)
   hand-build request states with `response_event_count=1` to mean "received
   response.created only". Commit `0a92cf39` ("retry protocol-only response
   stream failures") intentionally stopped counting protocol-only events
   (`response.created`/`in_progress`/`queued`) and tightened the replay arm to
   `response_event_count == 0`; the old hand-built states now encode "one
   visible event" and are correctly rejected.
3. Two doc pins (`test_readme_agent_prompt_keeps_onboarding_guardrails`,
   `test_agent_skills_pin_public_onboarding_and_account_operator_contracts`)
   assert exact hard line-break positions. Doc rewraps (`5b8e90ef` README
   prompt refresh, get-started skill rewrap) kept every guarded phrase but
   moved the wraps.
4. Two http-bridge first-event-timeout tests race the 1ms test keepalive tick:
   they set `request_state.response_id` while the event queue is empty, so the
   stream may yield a synthetic `response.in_progress` keepalive before the
   queued `response.created` (order/timing dependent — passes solo, fails in
   file runs).

## What Changes

- Tests only; no runtime behavior change.
  - Log-pin test now asserts the current spec'd contract: `code=` and
    `message=` present, secrets in the message redacted (`[REDACTED]`).
  - Replay tests updated to the post-`0a92cf39` accounting
    (`response_event_count=0` for "created received, nothing visible").
  - Doc pins assert whitespace-normalized phrases instead of hard wraps.
  - Bridge tests queue the created event before setting `response_id`,
    removing the keepalive race.
- Spec delta: the existing "Proxy 4xx/5xx responses are logged with error
  detail" requirement gains the redaction constraint that `ba538892`
  implemented but never codified.

## Impact

- Full unit suite returns to green; future doc rewraps and event-accounting
  work no longer break contract pins spuriously.
- Affected: `tests/unit/test_proxy_utils.py`,
  `tests/unit/test_proxy_http_bridge.py`,
  `tests/unit/test_public_release_docs.py`,
  `openspec/specs/proxy-runtime-observability/spec.md` (redaction clause).
