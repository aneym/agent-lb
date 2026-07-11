# Add stall forensics and event-loop lag telemetry

## Why

The 2026-07-11 studio outages were event-loop freezes that left no evidence:
the watchdog kickstarted the frozen process, destroying the state needed to
diagnose it. Root-causing required catching a stall live with manual `sample`
runs. Every future stall must self-document.

## What Changes

- App registers SIGUSR2 → faulthandler dump of all thread stacks appended to
  `~/.agent-lb/forensics/py-stacks.log`.
- A background `EventLoopLagMonitor` samples asyncio scheduling drift every
  second, exports `agent_lb_event_loop_lag_seconds` (gauge) and
  `agent_lb_event_loop_lag_events_total` (counter) on the existing metrics
  registry, and logs a rate-limited WARNING when lag exceeds the configurable
  threshold (`event_loop_lag_warning_threshold_seconds`, default 0.5s).
- Watchdog captures pre-kick forensics: SIGUSR2 to the app pid plus a native
  `sample` into `~/.agent-lb/forensics/`, with an events.log marker, a file
  cap, and all failures swallowed so the kick is never delayed.

## Impact

- Affected specs: proxy-runtime-observability
- Affected code: `app/core/forensics/`, `app/core/resilience/event_loop_lag_monitor.py`,
  `app/core/metrics/prometheus.py`, `app/core/config/settings.py`, `app/main.py`,
  `scripts/watchdog.sh`
