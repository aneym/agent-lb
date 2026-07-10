## Tasks

- [x] Detect remote-compaction turns at bridge submit (`compaction_trigger` input item) and track them on the websocket request state.
- [x] Accumulate streamed `compaction` output items per request in the bridge upstream-event relay (bounded cap).
- [x] Re-inject accumulated compaction items into the terminal `response.completed` envelope when the upstream websocket envelope carries no output items; leave non-empty envelopes untouched.
- [x] Add regression coverage at the failing surface (`/backend-api/codex/responses` streaming through the HTTP bridge): empty-envelope re-injection and non-empty-envelope passthrough.
- [x] Verify the fix fails-closed: regression test fails with the re-injection reverted.
- [x] Run focused and full validation gates (pytest, ruff, architecture ratchet).
- [x] Validate OpenSpec change locally.
  - `npx --yes @fission-ai/openspec@latest validate fix-bridge-compaction-completed-envelope --strict`
- [x] Live end-to-end verification: compaction probe through the deployed proxy returns a `response.completed` envelope carrying exactly one `compaction` item.
