# Cursor monthly budget pools

## Why
Cursor exposes no supported usage API. Leased runs need model and token receipts so observed monthly spend can be compared with subscription budgets.

## What Changes
- Record release usage and per-model UTC daily buckets; retain 62 days.
- Set registry tier and cycle day without changing authentication.
- Append separate Cursor-model and other-model budget pools using first-match model and price globs.
- Preserve availability pools and expose optional monthly spend, remaining, burn and reset fields.
