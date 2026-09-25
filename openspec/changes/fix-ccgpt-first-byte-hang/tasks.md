# Tasks

## 1. Implementation

- [x] 1.1 Refuse `thread` requests on the ccgpt route with `thread_unsupported_request`
- [x] 1.2 Bound the startup peek (first-frame timeout, content grace)
- [x] 1.3 `scripts/ccgpt_e2e_eval.py` real-session eval with receipts under `~/.agent-lb/evals/ccgpt-e2e/`

## 2. Validation

- [x] 2.1 Regression tests fail on the pre-fix code and pass after
- [x] 2.2 Live service restarted; e2e eval passes for Sol, Luna and a Sol subagent
- [x] 2.3 `scripts/claude_cache_eval.py` passes
