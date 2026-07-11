# Fix service fd exhaustion turning into client-visible 500s

## Why

On 2026-07-11 the MacBook agent-lb instance served hundreds of 500
`server_error` responses on `/v1/messages` and `/v1/ccdex/messages` with
`[Errno 24] Too many open files` and `unable to open database file`, plus 502
`upstream_unavailable` DNS failures — all symptoms of file-descriptor
exhaustion. The process held ~200 keep-alive upstream sockets against the
macOS launchd default limit of 256 open files.

Root cause: `scripts/install-service.sh` only *preserves*
`SoftResourceLimits`/`HardResourceLimits` when the existing plist already has
them. Studio's plist had hand-added limits (4096/8192) and stayed healthy; the
MacBook's plist was regenerated without them and ran at the 256 default. The
health endpoint kept returning 200 throughout, so launchers kept routing fresh
sessions to the broken instance.

## What Changes

- `scripts/install-service.sh` now defaults `SoftResourceLimits.NumberOfFiles`
  to 4096 and `HardResourceLimits.NumberOfFiles` to 8192 when generating the
  LaunchAgent plist, while still preserving any existing (customized) limit
  dictionaries.

## Impact

- Affected specs: `deployment-installation`
- Affected code: `scripts/install-service.sh`,
  `tests/unit/test_install_service.py`
- Every install/reinstall of the service plist now carries fd headroom by
  default; no instance can silently run at launchd's 256-fd default again.
