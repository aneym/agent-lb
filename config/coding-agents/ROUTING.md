# Coding-agent routing

Owner direction, 2026-09-21. `routing-table.json` is the executable ranking;
this policy explains it. Install from checkout sources only after review.

1. **Fable plans.** The driver owns requirements, decomposition, dispatch and
   reconciliation. Plan stays in the driver session, not a worker. Preserve
   scoped briefs, file ownership and independent acceptance.

2. **OpenAI builds; Astra validates.** Rankings, in order:

   | Class | Seat/model ranking |
   | --- | --- |
   | implement | implementer / gpt-5.6-terra / medium → implementer / gpt-5.6-sol / medium → cursor-seat / gpt-5.6-sol / high → cursor-seat / glm-* → opus-seat / claude-opus-5 |
   | mechanical | cursor-seat / cursor-grok-4.6-medium-fast → cursor-seat / glm-* → cursor-seat / kimi-* → opus-seat / claude-opus-5 |
   | verify | astra / gpt-6-astra; money paths require verifier / claude-opus-5 as a second read |
   | review | astra / gpt-6-astra → opus-seat / claude-opus-5 |
   | explore | cursor-seat / cursor-grok-4.6-low-fast → Explore / claude-sonnet-5 → opus-seat / claude-opus-5 |
   | research | astra / gpt-6-astra → opus-seat / claude-opus-5 → cursor-seat / cursor-grok-4.6-medium-fast |
   | computer | computer-use / gpt-6-astra → astra / gpt-6-astra |
   | council | astra / gpt-6-astra, driver as second voice |

   Jev difficulty ≥4 starts implementation at Sol; otherwise Terra. Model IDs
   and effort are separate fields. Opus is a scarce cross-vendor read, never
   the default builder. Its last-resort implementation slot remains explicit.

3. **Immediate eligibility, then reserve, then pace.** An account must be
   active with known short and weekly remaining percentages strictly above
   `--reserve` (default 20). Unknown data does not admit a pool. Every pick
   refreshes `/api/accounts`; failed refreshes fail closed. State older than
   300 seconds is never reused for pool admission. Pace cannot override these
   gates. `route pools` reports usable-now and minimum known short-window %;
   zero usable accounts means blocked, even with healthy weekly headroom.

4. **Classify, dispatch, verify.** Run `route classify "task" --paths path...`
   for one typed Jev decision, or `route dispatch-line "task"` for the prompt
   header and ledger entry. Send only task text and path names, never secrets
   or file contents. Exit 3 means `JEV UNAVAILABLE`: the caller falls back once,
   without retrying Jev. Manual routing uses `route pick <class>`; include
   `--task-text "task"` to identify money paths.

   Auth, RLS, billing, migrations, receipts, idempotency and tool boundaries
   require Astra **and** one Opus read. Neither is optional; a blocked required
   verifier blocks the decision. Carry the classifier's verifier list into
   the work plan: the dispatch header identifies only the primary seat.
   Read actual diffs and verify the failing product path before acceptance.
   Fixture evidence is not live provider or installation proof.

Fable, Opus and Sonnet all spend Anthropic capacity. The pulse counts all
three and reports missing telemetry. No policy or router command grants
permission to install, deploy, access credentials or widen a task's scope.
