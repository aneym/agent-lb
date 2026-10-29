---
id: verifier-receipts
product: agent-lb
owner: agent-lb-usage-audit-fixes
status: live
covers: ["route:/api/verifier/receipts/last"]
paths: ["app/modules/verifier_receipts/", "features/agent-lb/verifier-receipts.md"]
proofs: []
---
# Verifier Receipts

## Sub-features
A verifier seat records its verdict for one repo, head SHA and contract hash. agent-lb returns the latest verdict for that key, with the requests and tokens its job id spent.

## How to get to it
`POST /api/verifier/receipts` with `repo`, `headSha`, `contractHash`, `verdict` (PASS or FAIL) and optional `seat` and `jobId`. `GET /api/verifier/receipts/last?repo=&headSha=&contractHash=` returns the latest receipt and its job usage.

## Driving it
Use an HTTP client with dashboard access, as the T3 thread collector does.

## Gotchas
agent-lb only records verdicts. Deciding to skip a review on a matching receipt belongs to the verifier seat harness. Usage is requests and tokens, never cost.
