# Tasks

## 1. Launcher startup grace

- [x] 1.1 Detect connection-level local readiness failures and check the
      configured launchd job with a bounded command.
- [x] 1.2 Poll local readiness within the configurable startup grace while
      preserving the healthy and remote fast paths.
- [x] 1.3 Add focused launcher tests for grace entry, opt-out, and healthy-path
      behavior.

## 2. Startup attribution

- [x] 2.1 Compute non-negative untracked startup time and log it outside the
      phases JSON map.
- [x] 2.2 Add focused startup-recorder coverage for the computed value and log.

## 3. Validation

- [x] 3.1 Run launcher byte compilation and the live-service dry run.
- [x] 3.2 Run ruff, affected unit tests, and strict OpenSpec validation.
