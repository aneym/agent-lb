# Receipts name their lane, job and route decision

## Why
The 2026-10-17 overnight efficiency audit (rank 8) found that no lane could be held to a cost. $781.15 (23.3%) of $3,349 list-price spend had no lane. All 747 quota allocations showed $0 or null despite nonzero receipts. All 57 workflow readbacks had `usage_source=unavailable`. And 985 routing decisions covered only 496 job ids, with no way to join a decision to the work it routed.

## What Changes
- `request_logs` gains nullable `lane`, `job_id` and `dispatch_id` columns. The proxy reads `x-agent-lb-lane` and the new `x-agent-lb-job` per request. When a request lacks them, it inherits the lane and job last seen on the same client session. Neither header, nor `x-agent-lb-dispatch`, is forwarded upstream.
- `claude-lb-launch` sends `x-agent-lb-job` from `AGENT_LB_JOB` or the `job_id` in `AGENT_LB_DISPATCH`. Without `AGENT_LB_LANE`, it sends the lane from the dispatch document or the `LANE_DIR` basename.
- `agent-lb audit tokens` prefers the receipt lane. It reports `attribution` (`usd`, `lane_usd`, `unattributed_usd`, `unattributed_pct`, `coverage`, `by_source`) and `pct` on every waste kind. The `unattributed` waste kind now means spend with no lane source. Coverage and percentages are null, not 0, when there is no priced spend.
- `route dispatch` marks a decision `synthetic: true` under pytest, with `ROUTE_SYNTHETIC=1`, or when the job or caller starts with test, probe, synthetic, smoke or eval-. `agent-lb audit routing` reports `decisions`: the join rate of non-synthetic decisions to receipts (`dispatch_id`) and dispatch-ledger rows.
- The workflow-readback hook takes tokens from the Workflow usage, then from summed agent usage, then from agent-lb receipts for the session over the run's duration. `usage_source` names which one was used.
- `request_log_query.py` gains `--seat`, filtering on `caller_seat`.

## Impact
Affected specification: request-attribution (new). Code: app/core/utils/client_session.py, the client-session middleware, app/db/models.py plus one migration, the request_logs repository, the proxy header drop list, clients/claude-lb-launch, clients/route, app/audit_tokens.py, app/audit_routing.py, the workflow-readback hook, scripts/request_log_query.py. The factory `quota.json` writer, which still sums by user agent, is out of scope and is handed off.
