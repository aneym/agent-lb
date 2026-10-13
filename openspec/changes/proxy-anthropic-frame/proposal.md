# Proxy Anthropic frame requests

## Why
The Raise Desk watcher receives HTTP 405 for `/api/frame/db/agent` through agent-lb. It needs the selected account's OAuth credentials without holding a token itself.

## What changes
Add an authenticated frame route that forwards raw requests to Anthropic, preserves session affinity, recovers once on HTTP 401, and logs the caller seat with request kind `frame`.

## Impact
Changes the proxy API and Anthropic service. Checks use the HTTP test client, real test database, and a fake upstream.
