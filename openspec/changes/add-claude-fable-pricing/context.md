# Context

## Pricing Source

Anthropic's pricing page was checked on 2026-06-11 before implementation:

- Source: https://platform.claude.com/docs/en/about-claude/pricing
- Claude Fable 5: $10/MTok base input, $12.50/MTok 5m cache writes, $20/MTok 1h cache writes, $1/MTok cache hits and refreshes, $50/MTok output.
- Claude Mythos 5: same documented rates.

For newly released models, do not infer pricing from nearby model families or stale local tables. Search the web for a current source before implementation, prefer official provider docs, and record the source used in the change context before backfilling historical cost data.

## Backfill Assumption

`request_logs` stores cache creation token counts but not the cache duration tier. Existing Anthropic cost calculation defaults cache creation pricing to the 5-minute rate, so the Fable backfill uses the same default for consistency with future rows and existing Claude families.
