# Tasks

## 1. Implementation

- [x] 1.1 Read `CLAUDE_LB_CLAIM_TIMEOUT` via `_env_float` in
      `claim_session_route`, defaulting to 5.0s.

## 2. Validation

- [x] 2.1 `python3 -m py_compile clients/claude-lb-launch` passes.
- [x] 2.2 Dry-run round trip over a high-latency (DERP-relayed) path with
      `CLAUDE_LB_CLAIM_TIMEOUT=15` claims a session route successfully
      (3/3 consecutive launches on 2026-07-02).
