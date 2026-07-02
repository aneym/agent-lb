# Tasks

- [x] Diagnose all 7 failures against git history; confirm each is pin drift from an intentional shipped change (0a92cf39, ba538892, 5b8e90ef) or a test race — no production regressions
- [x] Update the log-pin test to the spec'd contract: code/message present, secret redaction asserted
- [x] Update the three replay tests to post-0a92cf39 protocol-only event accounting (`response_event_count=0`)
- [x] Whitespace-normalize the two public-release doc pins (phrases, not wrap positions)
- [x] De-race the two http-bridge first-event tests (queue created before setting response_id)
- [x] Spec delta: codify secret redaction in the proxy error-log requirement
- [x] Gates: `uv run ruff check app clients tests`; test_proxy_utils + test_proxy_http_bridge + test_public_release_docs fully green; full unit suite green; openspec validate
