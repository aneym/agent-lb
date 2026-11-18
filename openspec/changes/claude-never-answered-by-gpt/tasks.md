## 1. Implementation
- [x] 1.1 Remove the stand-in swap, store and pools endpoint from the server.
- [x] 1.2 Remove stand-in readers, intent inference and banners from the clients and scripts.
- [x] 1.3 Remove the stand_in_rollout policy key.

## 2. Validation
- [x] 2.1 Forced-429 Claude request on a live host returns 429 with no GPT text; a normal Claude call still serves.
