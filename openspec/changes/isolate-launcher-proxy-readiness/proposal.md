# Isolate launcher proxy readiness

## Why
Headless invocations share a stable cwd routing session. Per-launcher proxies currently share the session readiness file and atomic temporary file, allowing another launcher startup or cleanup to erase or replace the readiness receipt.

## Change
Keep the routing session unchanged; namespace each launcher readiness path with its parent process ID. Leave shared desktop proxy readiness unchanged.
