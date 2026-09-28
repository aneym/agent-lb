# Preserve service graceful-shutdown timeout on reinstall

## Why

The service installer rebuilds its plist from a fixed set of keys, dropping an existing `ExitTimeOut`. When absent, lb-restart assumes 20 seconds for its drain bound and launchd can SIGKILL the old service before in-flight streams complete.

## What Changes

- Preserve an existing integer service `ExitTimeOut` across reinstalls.
- On a fresh plist, derive `ExitTimeOut` from the resolved `UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN` plus 30 seconds, or default to 105 seconds when the setting is absent or not an integer.
- Cover the existing override, fresh default, and environment-derived timeout at the generated-plist boundary.

## Capabilities

### Modified Capabilities

- `deployment-installation`: installer preserves or generates the service shutdown window used by lb-restart and launchd.

## Impact

- Affected code: `scripts/install-service.sh`, `tests/unit/test_install_service.py`.
- Affected spec: `deployment-installation`.
