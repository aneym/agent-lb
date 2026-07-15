# Tasks — add-session-analytics

## 1. Bridge session identity (backend)

- [x] 1.1 Compute `_anthropic_request_session_id(payload, request.headers)` in
      `_ccgpt_messages_response` before translation; thread as optional
      `client_session_id` on the bridge request state.
- [x] 1.2 Log-write funnel persists `client_session_id or
request_state.session_id`; affinity keys, turn-state bookkeeping, and
      `_remember_websocket_previous_response_owner` unchanged.
- [x] 1.3 Regression tests: bridged Messages request with
      `X-Claude-Code-Session-Id` logs the real session id; Codex-CLI-shaped
      request (no client identity) still logs the synthetic turn id; sticky
      key derivation unchanged (assert key inputs).

## 2. Analytics API (backend)

- [x] 2.1 `GET /api/sessions/{sessionId}/analytics?windowMinutes=` per
      design.md: session aggregate, adaptive `bucketSeconds` + `series[]`
      by model+effort, `seats[]`, fixed-bin `latencyHistogram[]` +
      `tokensPerRequestHistogram[]`; SQL aggregation; dashboard auth; 404
      unknown session.
- [x] 2.2 List endpoint adds `sessions[].sparkline` (24-bucket request
      counts).
- [x] 2.3 Route-level tests: bucketing math, seat grouping by model+effort,
      histogram bins, sparkline length/sum, auth, 404.

## 3. Analytics view (frontend)

- [x] 3.1 Full-width analytics view replacing the modal when `?session=` is
      set: stat tiles, stacked timeline AreaChart by model, seat DonutChart +
      seat table (with seat display mapping util), latency + tokens/request
      histograms, recent requests. Reuse ChartTooltip / DonutChart /
      SparklineChart and `var(--chart-N)` theming.
- [x] 3.2 Sessions list sparkline column (optional field; hidden when
      absent).
- [x] 3.3 Tests: analytics schemas, seat mapping util, view render with
      mocked analytics payload, sparkline cell; focused 18 tests, full suite
      610, vite build green (frontend lane closeout).

## 4. Validation & ship

- [x] 4.1 ruff clean; backend 667-674 changed-file tests; frontend focused 18 + full suite 610 + vite build (coordinator-reproduced); `openspec
    validate add-session-analytics --strict` valid.
- [x] 4.2 Service kickstarted onto v2; live exercise on Postgres: sol smoke
      landed under coordinator session (gpt-5.6-sol rows in 588bfb7a);
      analytics returned 22 buckets @120s, per-effort seats, populated
      histograms, series-sums == session totals (92,141); list sparklines
      24-bucket. Closes the "Postgres dialect unexercised" residual.
- [x] 4.3 Verifier closeout: three real finds (retry identity loss, window
      aggregate mismatch, histogram bin edges), all fixed and re-verified;
      commit + push per standing auto-publish.
