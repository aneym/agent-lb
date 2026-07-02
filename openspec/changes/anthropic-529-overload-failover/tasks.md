# Tasks

- [x] 529 branch in the messages wake loop: record_error + upstream_529 request log + exclude account + jittered backoff + continue (no quota cooldown)
- [x] Terminal error surfaces overloaded_error/529 when the attempt budget exhausts on 529s (both the attempts-exhausted path and the candidates-ran-out-mid-wake path)
- [x] Backoff constants + injectable sleep/jitter (no real sleeps in tests)
- [x] Tests: failover to healthy account with no cooldown row and upstream_529 log; full-outage fails fast with overloaded type across bounded distinct-account attempts; backoff progression capped and skipped after final attempt; existing single-account native-529 test now covered by the mid-wake conversion
- [x] Gates: ruff; targeted anthropic suites; openspec validate; restart live service; smoke
