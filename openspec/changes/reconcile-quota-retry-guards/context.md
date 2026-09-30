# Context

## Incident and limits
2026-09-29: persisted generic usage recovered while routed adaptive-thinking Opus requests returned 503; a direct probe succeeded, then a controlled restart restored routed success. The pre-restart runtime was not captured, so its exact deadline/provenance is unverified. This change repairs reproducible code inconsistencies, not a claim that every incident failure was a legacy reset.

## Decisions and failure modes
The persisted reset is already normalized by `_bounded_retry_at`; the in-memory cooldown must use that same bound when a real `blocked_at` marker exists. Genuine <=1h retries and marker-free transient cooldowns remain unchanged. Example: a legacy weekly deadline on a refusal at T must not hold runtime selection after T+60 when persisted selection has already recovered.

Opus request refusals use `anthropic_opus` and `anthropic_opus_thinking`; Fable keeps top-model keys. An exhausted Opus observation cannot prove a current hard block because the router bounds old rows and the dashboard does not expose the live runtime selector. Report unknown, retain the raw row, and explain the bounded retry.

Manual probes have no thinking argument or generic runtime refusal scope/version contract. Their success therefore does not justify clearing another scope's block in this patch. S-11 is unchanged: no failed probe transitions are added.

## Test authoring gate
Cooldown cases protect selectable/not-selectable at the real `_state_from_account` to selector boundary; baseline raw cooldown reproduces exclusion after bounded expiry. Existing coverage seeds already-expired runtime cooldowns, not legacy future ones. Opus cases use the CLI HTTP observation boundary; baseline misses exact keys or trusts legacy headroom. No production test seam is added.

The failed-selection response also discarded `AccountSelection.retry_at` and hid a reason when no error code was set. The existing session-route integration owner now exercises a runtime-only cooldown alongside persisted failures; it fails baseline because the response drops the real retry deadline. The repair forwards aggregate exclusion counts (no account identities), keeps model cooldown precedence, and preserves the selector reason.

## First audit fix round
Audit reproduced a subsecond quantization bug: persisted blocked_at is integral but a valid runtime retry keeps fractions, so a genuine one-hour retry was classified as legacy and reduced to one minute for recovered ACTIVE accounts. The existing selector table now covers fractional markers, valid one-hour hold and expiry. Legacy classification will compare integral-second identities, preserving the exact runtime deadline.

The session-route integration also covers disjoint accounts excluded by requested-scope and runtime guards. Its deadline must be the earliest future live guard across either stage; snapshot windows remain fallback only and cannot replace a live retry. Baseline runtime30/request60 incorrectly advertised60; runtime120/request60 already advertised60. This is the first audit correction round.

## Second audit fix round
The prefilter `next_reset_at` also carries response-written extra-usage tripwires, not primary/weekly snapshots. A real session-route regression adds tripwire T+120 with another account's runtime retry T+900; baseline incorrectly returns T+900. Include the authoritative prefilter deadline in earliest-live retry calculation while leaving persisted account/usage snapshot fallback candidates separate. This is the second audit correction round.
