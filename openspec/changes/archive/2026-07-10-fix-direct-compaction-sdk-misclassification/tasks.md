## Tasks

- [x] Force `enforce_openai_sdk_contract=False` in the `responses()` route when
      the raw payload input contains a `compaction_trigger` item
      (`request_input_contains_compaction_trigger`).
- [x] Add regression coverage at the failing surface: compaction turn WITHOUT
      top-level `instructions` survives with its `compaction` output item
      (existing fixtures all set `instructions`, which masked the bug).
- [x] Verify fail-closed: the new test fails with the api.py change reverted.
- [x] Run validation gates (contract unit suite, bridge integration suite,
      ruff format/check).
- [x] Validate OpenSpec change strictly.
- [x] Live end-to-end verification on the deployed proxy: instructions-less
      compaction probe (exact failing shape, codex_exec user agent) reproduced
      `invalid_output_item` pre-fix and returns a `compaction` item in
      `response.completed` post-fix; the retry loop stopped (no loop-signature
      requests after 16:19Z). The wedged lanes could not be resumed for a
      literal re-run — their parent harness reaped them while paused.
