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
- [ ] Live end-to-end verification on the deployed proxy: instructions-less
      compaction probe returns a `compaction` item; previously wedged
      `codex exec` lanes resume and compact successfully.
