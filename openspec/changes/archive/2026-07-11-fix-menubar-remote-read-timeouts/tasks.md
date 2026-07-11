## 1. Timeout Policy

- [x] 1.1 Add a testable local-versus-remote dashboard read timeout policy.
- [x] 1.2 Keep health checks on the short failure envelope and probes on their existing long envelope.

## 2. Polling Load

- [x] 2.1 Sequence the heavy section reads so one refresh does not create a self-induced timeout storm.
- [x] 2.2 Preserve cancellation-neutral and genuine-failure section error behavior.

## 3. Regression Coverage and Deployment

- [x] 3.1 Add focused Swift tests for loopback and tailnet timeout selection.
- [x] 3.2 Run focused and full Swift tests plus strict OpenSpec validation.
- [x] 3.3 Rebuild/relaunch Studio and MacBook bundles and verify MacBook remote reads and visible section state.
- [x] 3.4 Record deployment evidence on issue #118 without closing the broader tracking issue.
