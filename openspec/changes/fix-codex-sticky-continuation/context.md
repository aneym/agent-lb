# Evidence and operating behavior

The incident receipts live in the Agent Rails program checks/codex-sticky-routing directory. Both terminal timestamps match agent-lb request errors within milliseconds. Both failed requests selected the same preferred account as their preceding successful turns. The request log does not store the submitted previous_response_id, so exact origin lineage cannot be independently reconstructed. Cross-account routing is not a confirmed incident cause.

Affinity priority remains turn-state, Codex session/conversation headers, then prompt-cache/sticky-thread hints. A previous_response_id resolves through the account index and successful request logs. Its owner wins over soft cache locality.

For the exact invalid-anchor rejection, existing full-resend recovery removes the anchor and retries once before output. Short continuations are not silently stripped. Codex receives codex_previous_response_stale; generic Responses clients receive stream_incomplete.

For quota rejection on a connected websocket, the request must be unstarted and eligible for one replay. The retained full input must either precede proxy anchor injection or match the known completed request's input prefix. File/image file references and explicit file pins forbid cross-account replay. The router restores that full input, removes previous_response_id, clears the old account preference, excludes the rejected account and updates its health through the normal handler. The same logical request reservation remains owned until final settlement. A short or unverified Codex continuation receives codex_previous_response_stale with an explicit instruction to resend full history; the proxy does not silently strip its delta. Generic Responses clients retain upstream_unavailable. File-dependent and conversation-bound requests retain owner-unavailable behavior. Already-started or already-retried requests cannot transparently rotate. This does not introduce account rotation for HTTP bridge requests or bypass preflight owner saturation.

Live rotation requires two actually usable accounts. An injected quota rejection is fault-injection evidence, not naturally exhausted quota. A source hash or mocked test is not a deployed or provider result.

## Qualification on Studio

Implementation commit 854aefdc passed 74 focused tests, Ruff and explicit-venv type checking. The retained extended continuity run passed 742 cases and failed one HTTP idle-prune timing test; independent isolated baseline 7134c173 fails identically. The change does not waive that failure.

The final real Codex Sol fault-injection session completed 12 separate tool turns after both stale-anchor and quota rejection, with the isolated quota health mutation suppressed because only one real account was usable. This proves client reset and full-history resend, not real account rotation. After selective three-file deployment and the normal launchd graceful restart, another actual Codex session through shared port 2455 completed all 12 steps without injection. Runtime hashes matched and 449 unrelated Python files remained unchanged. The shared source checkout was not edited.

True live cross-account rotation remains blocked on a second usable existing account. Source review remains in draft PR 3; no exact-SHA hosted checks were reported at closeout. The Rails Application gates workflow does not exist in agent-lb, and a local test pass is not a substitute. Shared-source integration is needed before a future broad runtime sync, or that sync will overwrite the selective runtime patch.
