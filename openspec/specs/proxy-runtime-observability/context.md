# Proxy Runtime Observability Context

## Purpose and Scope

This capability defines what operators should be able to see in the live server console while debugging proxy traffic.

See `openspec/specs/proxy-runtime-observability/spec.md` for normative requirements.

## Decisions

- **Timestamps are always on:** timestamped console logs are a baseline operator need, not a debug-only feature.
- **Request tracing is opt-in:** outbound request summary and payload tracing remain configurable because payload logs can be noisy or sensitive.
- **Error logs must be correlated:** request id, endpoint, status, code, and message are the minimum useful fields for debugging 4xx/5xx failures.

## Operational Notes

- Use request ids to correlate inbound proxy logs, outbound upstream traces, and client-visible failures.
- Prefer summary tracing in normal debugging sessions; enable payload tracing only when the exact normalized outbound request matters.
- For direct compact `5xx` failures, look for `proxy_compact_failure` alongside `upstream_request_complete`; together they show the compact failure phase, failure detail, exception type, retry metadata, and affinity source.

## Backlog: spend-origin analytics (Alex feature request, 2026-07-10)

Track the origin of spend end to end and surface it in the dashboard: who the
biggest consumers are, over time, with a cheap deterministic classification of
consumers. Not started — create a full OpenSpec change (with spec deltas) when
picked up.

- Consumer label derived at request-log write time from `useragent_group` +
  api key + configurable mapping rules (`codex_exec` → "headless lanes",
  `OpenAI/Python` → "hermes", `Codex Desktop` → "desktop", Claude Code UA →
  "claude sessions"); unknown agents land in "other" with the raw string
  visible. Pure rules, no model calls.
- Rollup aggregates (consumer × day × model family: requests, output tokens,
  uncached input, cost) so dashboard queries stay O(days).
- Monochrome dashboard graphs per DESIGN.md: stacked daily output-token bars
  by consumer, top-N consumer table, per-consumer sparkline drilldown,
  limit-window pressure overlay.
- Ops one-clickers: "who burned the pool today", "which consumer drove
  account X into its window".
- Origin: the 2026-07-10 usage audit answered these questions only via
  hand-written SQL; a consumer-level graph would have surfaced that day's
  compaction retry loop in minutes.
