## 1. Implementation

- [x] 1.1 Price Claude cap usage from the Anthropic table with the cache split and tier
- [x] 1.2 Refuse unpriced models under a `cost_usd` cap (403 on every proxy surface, skip in warmup)
- [x] 1.3 Keep the reservation for consumed usage that cannot be priced
- [x] 1.4 Integration test over `/v1/messages` and unit money-math table
- [x] 1.5 Sandbox-port check `scripts/lb-spend-cap-check`
- [x] 1.6 Fix round: strict list-price lookup for the cap guard, refuse blank models, route binary websocket response.create through the text path
- [x] 1.7 Slice 2: strict OpenAI admission; file sentinel exemption only from trusted file routes; positive spend reservation floor
- [x] 1.8 Sandbox HTTP regressions for gpt-5.99, gpt-5.1-unpriced, sentinel misuse, and capped file registration/finalize
