## 1. Shim hardening

- [x] 1.1 Add the claude-desktop-proxy delta for shim connection hygiene.
- [x] 1.2 Add idle client-connection reaping (handler timeouts + MITM dup'd-socket timeout) with the blind-tunnel exemption.
- [x] 1.3 Raise the shim soft fd limit at startup, best-effort.
- [x] 1.4 Scope upstream retries to the connect phase and close upstream responses explicitly.

## 2. Validation and rollout

- [x] 2.1 Byte-compile, ruff, harness tests (idle reap, MITM handshake under timeout, fd-limit raise), dry-run round trip, strict OpenSpec validation.
- [ ] 2.2 Restart the shared desktop proxy on new code, prove fd count stays flat, push main, fast-forward other machines.
