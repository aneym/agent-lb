## 1. Implementation

- [x] 1.1 Add `_TunnelBucket` earliest-departure-time pacer with burst floor, small-write sleep bypass, and env-driven construction (`_tunnel_bucket_from_env`, non-finite guard).
- [x] 1.2 Thread the bucket through `_splice` (per-chunk pacing, byte totals) and attach it to the proxy server in `run_lb_proxy`.
- [x] 1.3 Log tunnels that relayed >= 8 MB from `_ProxyHandler._tunnel` with a single atomic stderr write.

## 2. Validation

- [x] 2.1 Unit tests: env parsing (default, disable, NaN), pacing rate, small-write bypass with schedule debit, splice byte accounting.
- [x] 2.2 `py_compile` + `ruff check app clients` clean; launcher unit suite green.
- [x] 2.3 Live exercise: shaped bulk download through the shared shim measures near the configured rate; API path through the LB unaffected.
