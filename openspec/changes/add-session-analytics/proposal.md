# Add session analytics

## Why

The session map (change `add-claude-session-map`) ships flat tables and — the
bigger gap — misses all subagent seat traffic: Sol-alias `/v1/messages`
requests are served through the HTTP bridge, whose logging path overwrites the
client session id with a per-request synthetic `http_turn_<hex>`
(`streaming.py:493`), so the sessions rollup (which rightly excludes synthetic
ids) never sees implementer/verifier seat usage. Operators cannot answer "what
seats did this session use, what did each model consume, how did the session
unfold over time" — the owner's explicit ask.

## What Changes

- Bridge-path request logs prefer real client session identity (Anthropic
  `metadata.user_id` JSON / `X-Claude-Code-Session-Id`) over the synthesized
  turn id — logging only; sticky/affinity keys and turn-state bookkeeping are
  untouched.
- Per-session analytics API: time-bucketed series by model, per-seat
  (model + reasoning effort) totals, latency and tokens-per-request
  distributions.
- Sessions list gains lightweight per-session sparkline series.
- The dashboard session detail becomes a full analytics view: stat tiles,
  stacked timeline, seat donut + seat table, distribution histograms, recent
  requests.

## Capabilities

### Modified Capabilities

- `proxy-runtime-observability`: bridged Messages requests persist client
  session identity; sessions API exposes analytics aggregates.
- `frontend-architecture`: Sessions page renders the analytics view.

## Impact

- `app/modules/proxy/api.py` (`_ccgpt_messages_response` area),
  `_service/http_bridge/` request-state plumbing, `_service/request_log.py`
  writer preference.
- `app/modules/sessions/` (analytics repository/service/api/schemas).
- `frontend/src/features/sessions/` (analytics components), reusing
  `components/sparkline-chart.tsx`, `components/donut-chart.tsx`, reports
  chart conventions.
- Regression coverage: bridge logging path, analytics endpoint, seat/effort
  aggregation, page render.
