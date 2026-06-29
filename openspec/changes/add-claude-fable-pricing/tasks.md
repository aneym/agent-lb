## 1. Spec

- [x] 1.1 Add the API-key/accounting requirement for Claude Fable 5 pricing recognition.
- [x] 1.2 Record the fresh-pricing-source rule in change context.

## 2. Implementation

- [x] 2.1 Add Claude Fable 5 and Mythos 5 canonical pricing and aliases.
- [x] 2.2 Add unit coverage for pricing resolution and cache-aware calculation.

## 3. Data Repair

- [x] 3.1 Backfill existing `claude-fable-5*` request-log `cost_usd` values in the live database.
- [x] 3.2 Re-read live dashboard/report totals after backfill.

## 4. Validation

- [x] 4.1 Run targeted pricing tests.
- [x] 4.2 Run strict OpenSpec validation.
      - 2026-06-14: `npx --yes @fission-ai/openspec@latest validate add-claude-fable-pricing --strict` -> valid.
