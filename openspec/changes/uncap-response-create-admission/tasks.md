- [x] Change the default account response-create cap to unlimited (`0`) while
  preserving explicit nonzero cap behavior.
- [x] Update operator-facing env example comments for account-local caps.
- [x] Update OpenSpec requirements so response-create and stream caps are both
  opt-in by default.
- [x] Add focused tests proving response-create leases remain pressure when the
  default cap is disabled.
- [x] Validate the OpenSpec change, focused tests, and live daemon behavior.
- [x] Recover the live LaunchAgent with the full Postgres, dashboard-auth, and
  Tailnet runtime environment after the installer env clobber.
- [x] Harden the macOS installer so it preserves existing LaunchAgent
  environment variables, custom arguments, and resource limits.
- [x] Add focused installer coverage proving existing runtime configuration is
  preserved while missing metrics defaults are added.
- [x] Ensure the default local runtime installs the Prometheus client required
  by installer-owned metrics defaults.
- [x] Include normalized error code/message in structured proxy error-response
  logs.
- [x] Emit a safe startup runtime fingerprint for database, auth, metrics,
  cap, trusted-proxy, and HTTP bridge posture.
