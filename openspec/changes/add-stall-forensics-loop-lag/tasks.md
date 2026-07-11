# Tasks

## 1. Implementation

- [x] 1.1 SIGUSR2 faulthandler stack-dump registration at app startup.
- [x] 1.2 Event-loop lag monitor task + Prometheus gauge/counter + rate-limited
      warnings.
- [x] 1.3 Watchdog pre-kick forensics capture (signal + native sample + marker,
      bounded, failure-swallowing, capped dir).

## 2. Validation

- [x] 2.1 Unit tests for lag monitor and stack-dump registration; `bash -n`
      on watchdog.
- [x] 2.2 Deploy to studio + laptop; verify SIGUSR2 grows py-stacks.log and
      the lag gauge appears on :9090/metrics.
