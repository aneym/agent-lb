# Measure Claude quota use by caller

Claude cache reports show tokens and API prices. Alex asked on 2026-10-14 to measure the subscription quota consumed by each caller. Preserve the upstream unified rate-limit headers and report changes in 5h and weekly utilization per account.

## Changes

- Keep all Anthropic unified response headers and their observation time on request receipts.
- Add `lb-cache --quota --since 24h [--json]` with per-account caller shares and five leading consumers.

## Impact

Request logs gain nullable header evidence. Request payloads and account selection stay unchanged. The report reads receipts only.
