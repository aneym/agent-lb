# Routing source review

## Decisions
OpenAI builds, Astra validates, Fable plans. The explicit last-resort Opus
implementation slot is retained; it is no longer the first choice. Money-path
classification requires Astra and an Opus read; missing verifier capacity fails
closed, including for planning tasks.

The installed CLI/table/hooks were not tracked. They were imported from the four
paths explicitly named in the brief. The table contained six unknown-class
overrides rather than three; all six were removed. The unrationed-Opus sentence
was in seat-guard.py, so that tracked copy was corrected too.

Jev uses the TypeSafe System One API wire shape from rails/jev.py: one state,
one questions object, choice/score/noul primitives. The score is zero-based on
the wire and translated to difficulty 1-5. Only task text and path names enter
state. The configured environment key is used only in the Authorization header;
no key or credential files were read during implementation. No live Jev call
or health check was run.

## Example
`route classify "Fix billing receipts" --paths app/billing.py --json` selects
Terra medium for ordinary difficulty and requires Astra plus an Opus read.
Difficulty at least four starts at Sol medium. `route dispatch-line` prints
only the primary-seat header; its ledger decision includes the required reads.
The driver must schedule those reads before acceptance. The ledger records a
routing decision, not proof that a worker was launched or completed; its source
field distinguishes it from seat-guard records. Its prompt hash covers the task
text, not a later expanded Agent prompt.

Every pick refreshes /api/accounts without persisting local state. If refresh
fails it refuses, even if a recent aggregate cache exists. A successful endpoint
read is account-state refresh evidence, not a new upstream quota probe. Unknown
provider pools, missing windows and percentages at or below reserve are blocked.
The endpoint exposes resetAtPrimary/resetAtSecondary at account level, not inside
usage. Claude candidates also use the runtime's model/effort quota mapping and
gate on every reported applicable additionalQuotas window; Fable adds its scoped
weekly quota. Those gates are candidate-specific, so an exhausted top-thinking
window does not erase capacity for a compatible model in the general pool.
The limiting window identifies the bottleneck of the best eligible account;
it is not a promise that the load balancer will select that account.

## Verification and boundaries
Eight offline unittest tests exercise CLI handlers with /api/accounts fixture
JSON, a mocked TypeSafe transport, temporary HOME and ledger paths inside this
checkout, and network connections disabled. Tests preserve the prior historical
Fable fixture assertion while replacing two obsolete policy-table assertions.

Command: `python3 -m unittest discover -s tests/unit -p test_coding_agent_routing.py -v`
Result: 8 tests passed. Python byte-compilation and git diff --check passed.

Not exercised: install-policy.py execution, live hooks, live LB/Jev providers,
strict OpenSpec validation and Ruff. The worktree has no .venv; openspec and ruff
are unavailable on PATH. No tools were installed. The change remains active
and the PR is draft pending strict validation and the driver's install review.
The installer was inspected and compiled only, never executed.

No deployment, live configuration writes, credential reads, access grants,
Herdr changes, or agent messages were performed. Public onboarding/support and
server/API surfaces are unchanged; no public documentation fan-out is needed.
