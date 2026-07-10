# Spend-origin analytics: classify consumers cheaply, graph the biggest spenders

Status: **queued — proposal only, not started** (Alex feature request, 2026-07-10).

## Why

The 2026-07-10 usage audit answered "what is eating my codex limits" only via
hand-written SQL against `request_logs`. Everything needed already exists in
the schema — `useragent_group`, `useragent`, `session_id`, `account_id`,
`cost_usd`, token columns — but there is no first-class consumer model or
dashboard surface. A consumer-level graph would have surfaced that day's
compaction retry loop in minutes instead of hours.

## What (sketch to be refined when picked up)

- **Consumer classification (cheap, deterministic):** derive a `consumer`
  label at request-log write time from useragent_group + api_key +
  configurable mapping rules (e.g. `codex_exec` → "headless lanes",
  `OpenAI/Python` → "hermes", `Codex Desktop` → "desktop", Claude Code UA →
  "claude sessions"). Rules editable in the dashboard; unknown user agents
  fall into "other" with the raw string visible. Pure rules, no model calls.
- **Aggregates:** rollup table or materialized view (consumer × day × model
  family) of requests, output tokens, uncached input, and cost, so dashboard
  queries stay O(days), not O(requests).
- **Dashboard graphs (monochrome per DESIGN.md):** stacked daily output-token
  bars by consumer; top-N consumer table over a selectable window;
  per-consumer drilldown sparkline; limit-window pressure overlay.
- **Ops one-clickers:** "who burned the pool today", "which consumer drove
  account X into its window".

## Impact (when implemented)

- Affected specs: likely `proxy-runtime-observability` + a dashboard/frontend
  capability; schema change (rollup/labels) ⇒ Alembic migration gates apply.
- Prior art: the 2026-07-10 audit SQL patterns; the `compaction_retry_loop`
  breaker audit events.
