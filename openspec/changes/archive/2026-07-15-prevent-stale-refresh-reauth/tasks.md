## 1. Repository Concurrency Primitives

- [x] 1.1 Add a forced account reload that bypasses stale SQLAlchemy identity-map state
- [x] 1.2 Add compare-and-swap support to token persistence and roll back lost writes

## 2. Refresh Reconciliation

- [x] 2.1 Use the forced reload before permanent authentication status transitions
- [x] 2.2 Persist successful token rotations conditionally and converge on a newer winning row
- [x] 2.3 Protect non-refresh token-set rewrites from overwriting concurrent rotations

## 3. Regression Coverage

- [x] 3.1 Test permanent refresh failure reconciliation against newer stored credentials
- [x] 3.2 Test repository reload and conditional token-write behavior
- [x] 3.3 Run relevant tests, lint, strict OpenSpec validation, and live account probes
