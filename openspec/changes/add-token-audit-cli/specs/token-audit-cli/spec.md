## ADDED Requirements
### Requirement: Read-only token audit
The CLI SHALL exclude deleted receipts, use a read-only transaction with a
statement timeout, and report token classes without double counting reasoning.
#### Scenario: Cached OpenAI input
- WHEN input includes cached tokens
- THEN fresh input is input minus clamped cached input.
### Requirement: Honest attribution and pricing
The CLI SHALL prefer recorded dollars, reconstruct recognized missing prices,
retain unpriced counts, and label unresolved purpose unknown. It SHALL NOT emit
credentials, account emails, or transcript prompt text.
#### Scenario: Missing price
- WHEN a recognized model has complete usage but no recorded dollars
- THEN the report marks its price recomputed, not observed.
### Requirement: Repeatable reports
The CLI SHALL support JSON, text and self-contained HTML, session rankings,
exclusive waste candidates, and safe pane snapshots.
#### Scenario: Historical attribution
- WHEN a pane has closed but its session has a saved snapshot
- THEN the audit can recover its pane label without a live pane.

### Requirement: Agent accounting
The CLI SHALL split Anthropic session dollars and token classes using deduplicated
transcript input-side cost, reconcile the remainder on lead, and expose seat and
kind dimensions. Workflow agents inherit the lead lane and get workflow purpose.
#### Scenario: Interleaved subagents
- WHEN a session contains lead and subagent receipts
- THEN cache events are detected separately for each transcript sequence.
### Requirement: Routing measurements
The CLI SHALL provide `audit routing` with UTC windows, JSON, text and HTML,
previous-window comparison and rows grouped by seat, model, effort and harness.
It SHALL reuse token-audit receipt loading and quota allocation, distinguish
Claude Code bridge from native Codex clients, and preserve raw useragent groups.
Fold accepted-unit costs SHALL include all matched stages and failed pieces,
separate pools, and mark samples below ten provisional without movement flags.
#### Scenario: Quarter movement with sufficient samples
- WHEN both windows have at least ten samples and a metric changes by more than 25 percent
- THEN the moved list includes both values and sample counts.
#### Scenario: Missing lane title
- WHEN a client session has no pane or transcript title
- THEN its lane label uses the client and eight-character session prefix, never prompt text.

### Requirement: Exclusive waste premiums
The CLI SHALL assign one cache kind per write above 20k tokens, price it as the
premium over cache reads, and exclude first writes from waste totals. Other waste
assignments SHALL be exclusive and retries SHALL exclude the original attempt.
#### Scenario: Expired cache followed by account switch
- WHEN a 5m write occurs after a gap above 300 seconds with a different account
- THEN only ttl_expiry is assigned.
