# Design — add-session-analytics

## Bridge session identity (logging only)

At `_ccgpt_messages_response` (`app/modules/proxy/api.py:845-877`) the original
`AnthropicMessageRequest` payload and untouched `request.headers` are still in
scope before `claude_to_responses()` discards them. Compute
`_anthropic_request_session_id(payload, request.headers)`
(`anthropic_service.py:1186`, cross-module underscore import precedent at
`api.py:123`) once, and thread it as a new optional `client_session_id` on the
request state through `_stream_responses` → `stream_http_responses`.

At the single log-write funnel (`_service/request_log.py` writers, fed from
`request_state`), the persisted value becomes
`client_session_id or request_state.session_id`. The synthetic
`downstream_turn_state` continues to populate `request_state.session_id`
itself, so:

- sticky/affinity keys (`bridge_session_key`, `streaming.py:363-397`) — never
  derived from the logged value — are untouched;
- `_remember_websocket_previous_response_owner` (`websocket/mixin.py:3232`)
  keeps reading `request_state.session_id` unchanged;
- Codex-CLI flows (no Anthropic client identity) behave exactly as before.

The sessions rollup's synthetic-id exclusion stays; real ids now flow through
it for seat traffic.

## Analytics endpoint

`GET /api/sessions/{sessionId}/analytics?windowMinutes=` (dashboard auth,
sessions module conventions):

- `session`: the existing aggregate shape.
- `bucketSeconds` + `series[]`: adaptive buckets targeting ~48 points
  (`bucketSeconds = max(60, ceil(windowSeconds / 48 / 60) * 60)`); each bucket
  `{bucketStart, byModel: [{model, reasoningEffort, requests, outputTokens,
cachedInputTokens, costUsd}]}`. Epoch-floor bucketing, dialect-branched like
  `reports/repository.py:53-58` (Postgres `func.floor(extract(epoch)/N)*N`,
  sqlite `strftime('%s')/N*N`).
- `seats[]`: GROUP BY model + reasoning_effort — requests, input/output/cached
  tokens, costUsd, errors. Seat _naming_ is a frontend display mapping
  (mirrors the ROUTING.md lineup; raw model+effort is the API truth).
- `latencyHistogram[]` / `tokensPerRequestHistogram[]`: fixed deterministic
  bins in SQL CASE (latency ms: 0-1k, 1-2k, 2-5k, 5-10k, 10-30k, 30-60k,
  > 60k; tokens/request: <100, 100-500, 500-2k, 2k-10k, 10k-50k, >50k) —
  > `{label, count}`. Fixed bins keep the query one pass and the axes stable
  > across sessions.
- All aggregation in SQL, one round trip per block, same eligibility clause as
  the rollup.

List endpoint addition: `sessions[].sparkline: number[]` — request counts in
24 fixed buckets across the window (cheap GROUP BY over the new
`idx_logs_session_time` index).

## Frontend

`/sessions?session=<id>` switches the page from list to a full-width analytics
view (no modal — charts need room; the `/s/` short-link contract is
unchanged). Layout, top to bottom, reusing repo chart conventions
(`var(--chart-N)` grayscale, `ChartTooltip`, dasharray series
differentiation, manual svg legends):

1. Stat tiles (reports-summary-cards pattern): duration, requests, tokens
   (with cached share), cost, errors, seats used.
2. Stacked `AreaChart` timeline: output tokens per bucket by model (top 4
   models + "other").
3. Seat row: `DonutChart` (`components/donut-chart.tsx`) of cost by seat +
   seat table (seat label, model, effort, requests, tokens, cost).
4. Distributions: two `BarChart` histograms (latency, tokens/request) themed
   like the reports charts.
5. Recent requests table (existing component).

Sessions list rows gain a `SparklineChart`
(`components/sparkline-chart.tsx`) cell fed by `sparkline`.

Seat display mapping (frontend util, fallback = raw model):
`gpt-5.6-sol`+`medium` → Implementer · `gpt-5.6-sol`+`xhigh` → Verifier ·
`claude-sonnet-5` → Explore/scout · `claude-fable-5` → Driver.

## Explicitly out of scope

- True subagent-instance counts (client-side transcript enrichment; the LB
  sees seats, not agent instances) — backlogged with the fleet-monitor
  prototype as prior art.
- Retagging historical synthetic rows.
