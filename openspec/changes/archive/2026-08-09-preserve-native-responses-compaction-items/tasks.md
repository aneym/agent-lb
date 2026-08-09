## Tasks

- [x] Confirm with a live provider probe that upstream actually issues a `compaction` output item when `context_management` is sent (Codex-native verbatim route).
- [x] Add failing public-contract regression coverage: streamed `compaction` item survives `/v1/responses` normalization, lands in the backfilled terminal envelope, and survives the non-streaming collect path.
- [x] Add coverage that top-level `context_management` and replayed `compaction` input items reach the upstream payload unchanged.
- [x] Add coverage that non-compaction text-less output items keep their existing drop + `invalid_output_item` behavior.
- [x] Implement the minimal change: treat `compaction` as a public passthrough output item type.
- [x] Run focused and broader proxy test suites, `ruff check`, and `py_compile`.
- [x] Validate the OpenSpec change strictly.
- [x] Restart the live service and run a real end-to-end: obtain a genuine provider-issued encrypted compaction checkpoint through the public route, persist it with Hermes state machinery, reopen an isolated session, replay it through Agent LB, and get a successful provider response.
