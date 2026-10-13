## Purpose

Run Meta's Muse Code through a scoped factory seat with pinned distribution and model selection, provisioned authentication, and verifiable execution receipts.

## ADDED Requirements

### Requirement: Muse family resolution uses a recorded model pin
`route resolve muse-latest` SHALL return the Muse alias's pinned model without requiring an authenticated catalog. It SHALL refuse a missing pin, a pin outside the declared family, or a blocked or retired pin. Adding this alias SHALL leave every default class ladder unchanged.

#### Scenario: Offline resolution
- **WHEN** the catalog is unavailable and `muse-latest` pins `muse-wonder-1.0`
- **THEN** resolution returns `muse-wonder-1.0`

#### Scenario: A blocked model is pinned
- **WHEN** the pinned Muse model matches a blocked pattern
- **THEN** resolution fails without substituting another model

### Requirement: Muse CLI requires provisioned authentication
The provider CLI guard SHALL refuse executable `muse` commands without a nonempty `META_API_KEY` or the existing exact command approval record. It SHALL preserve shell wrapper, substitution and heredoc handling and SHALL emit no credential values.

#### Scenario: Personal sign-in is not provisioned
- **WHEN** `muse exec` runs without a provisioned key or exact approval
- **THEN** the guard refuses it with the personal-login floor

#### Scenario: A provisioned key is supplied
- **WHEN** `META_API_KEY` is nonempty in the command environment
- **THEN** the guard permits the command without printing the key

### Requirement: Muse installation uses durable pinned tools
The box tools installer SHALL install a verified, pinned Muse native binary under the durable tools directory. Runtime release replacement SHALL preserve it. The seat launcher SHALL forward the scoped contract through `seat-run` and SHALL not change the default model ladders.

#### Scenario: Factory runtime replacement
- **WHEN** a factory runtime release changes after Muse installation
- **THEN** the pinned Muse executable remains available from the durable tools directory

### Requirement: Muse dispatch and capacity are observed
Agent-lb SHALL expose a Muse pool with observed usage and vendor limit readback. Unavailable measurements SHALL remain unknown. A real Muse seat run SHALL record its person, machine, model, session, outcome and usage without secrets, and SHALL return the scratch worktree diff through the factory runner.

#### Scenario: A scratch task completes
- **WHEN** a provisioned Muse seat creates a one-line file in the assigned scratch git repository
- **THEN** its returned diff contains that file and agent-lb logs identify the run

#### Scenario: No quota readback exists
- **WHEN** vendor quota cannot be measured
- **THEN** the pool does not fabricate a remaining percentage or limit

### Requirement: Studio login remains local
The Muse seat SHALL use Alex's existing Studio login without opening credential or settings files. It SHALL clear `META_API_KEY` for a login account so an environment key cannot replace that session. A box account SHALL require a provisioned `META_API_KEY`; browser sessions SHALL never be leased to boxes. The CLI build SHALL be `1.4.7-R6101.1`. Runs SHALL trust the assigned workspace explicitly, retain sandboxing, and disable Contributor mode for that run.

#### Scenario: Studio login dispatch
- **WHEN** a Muse login account runs on macOS
- **THEN** the seat invokes the pinned CLI under the existing session with the overriding API key removed

#### Scenario: A box lacks a provisioned key
- **WHEN** a box registers Muse without `META_API_KEY`
- **THEN** registration fails before dispatch and requests box provisioning only

### Requirement: Unobserved JSONL fields remain unknown
Until a real pinned CLI transcript establishes the event contract, the adapter SHALL use plain exec output and an assigned session UUID. Token counts SHALL remain null. A successful run SHALL count as observed usage and a vendor limit error SHALL cool the account down using the existing CLI-seat policy.

#### Scenario: Plain exec completes
- **WHEN** the pinned CLI exits successfully with nonempty output
- **THEN** the receipt records its assigned session and result, with null token counts
