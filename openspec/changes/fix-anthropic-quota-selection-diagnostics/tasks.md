## Tasks

- [x] Add Anthropic selection failure diagnostics for model-quota prefilter exclusions.
- [x] Preserve model-quota detail in the Claude LB launcher friendly error text.
- [x] Add regression coverage for an active Anthropic account excluded by `anthropic_top` cooldown.
- [x] Run focused test coverage for the diagnostics change.
- [x] Validate OpenSpec change locally.
  - `npx --yes @fission-ai/openspec@latest validate fix-anthropic-quota-selection-diagnostics --strict`
    passed.
