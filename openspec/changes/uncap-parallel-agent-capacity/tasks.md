- [x] Baseline the live LB with the staircase eval and record the receipts.
- [x] Remove the per-host cap on the shared HTTP pool and raise the total to 1024.
- [x] Release paced upload bytes in TLS-record chunks.
- [x] Retry edge-rejected websocket handshakes with jittered backoff.
- [x] Widen that retry from six attempts to a three-minute window after N=200 still surfaced 30 refusals.
- [x] Apply the same retry to streamed Responses requests sent upstream over websocket (proxy.py).
- [x] Add regression tests that fail before each fix.
- [x] After the restart, rerun the eval at 100 and 200 and compare it with the baseline.
  Receipt 20260925T215443Z-post-restart-acceptance: 25/50/100/200 on codex, ccgpt and anthropic,
  1500 of 1500 ok, 0 errors; 46 requests rode out edge 403s for up to 8 attempts (p95 ~75 s at 200).
