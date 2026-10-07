# fix-cost-cap-claude-list-price

## Why

API key `cost_usd` caps priced every Claude request at $0: cap settlement only read the
OpenAI price table, so the reservation for a Claude request was refunded in full at
settlement and a capped Claude caller never tripped (bops S16 spend floor, 2026-10-07).

## What Changes

- Cap accounting prices Claude requests at list price from the Anthropic price table,
  using the response's input, output, cache-write (5m or 1h) and cache-read tokens.
  Subscription accounts still cost $0 in fact; the cap reads list price regardless.
- A request on a model with no list price, made with a key that has a `cost_usd` cap
  covering that model, is refused before any upstream call (HTTP 403,
  `model_unpriced_under_cost_cap`, message naming the model and the cap). It is never
  metered at $0.
- Usage that cannot be priced at settlement (for example a model-less request that still
  consumed tokens) keeps its reservation instead of settling at $0.
- Uncapped keys, keyless traffic and token-type limits are unchanged. OpenAI cap pricing
  is unchanged.

## Impact

- Affected specs: `api-keys`
- Affected code: `app/modules/api_keys/service.py`, `app/modules/proxy/api.py`,
  `app/modules/proxy/_service/api_key_usage.py`, `app/modules/proxy/anthropic_service.py`
  (settlement arguments only; the upstream payload is untouched),
  `app/modules/quota_planner/warmup.py`
- No schema change: request-log and receipt `cost_usd` already hold list price.
