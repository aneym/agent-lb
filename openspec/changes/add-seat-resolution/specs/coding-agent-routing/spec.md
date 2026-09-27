## ADDED Requirements

### Requirement: Every dispatchable seat has one seats entry
The routing verifier SHALL require a seats entry that agrees with each seat definition and its class entries.

#### Scenario: Missing chain seat
- **WHEN** a chain seat has no seats entry
- **THEN** verification fails `seats-map`

#### Scenario: Vendor disagreement
- **WHEN** a seats entry vendor disagrees with a chain entry
- **THEN** verification fails `seats-map`

#### Scenario: Missing standalone model
- **WHEN** a no-chain seat lacks a model and effort
- **THEN** verification fails `seats-map`

#### Scenario: Missing auditor flag
- **WHEN** an auditor lacks a boolean `needs_test_run`
- **THEN** verification fails `seats-map`

### Requirement: A seat resolves at a pinned rules commit
The router SHALL return the seat and definition resolved from one rules version.

#### Scenario: Default applied commit
- **WHEN** APPLIED names a usable commit
- **THEN** route seat resolves from that commit

#### Scenario: Pin older commit
- **WHEN** a caller gives `--rules` for an older commit
- **THEN** the seat and definition hash come from that older commit

#### Scenario: Installed fallback
- **WHEN** no usable APPLIED exists but the installed table does
- **THEN** route seat answers with source `installed` and a null commit

#### Scenario: Disagreeing classes
- **WHEN** a seat has different efforts across classes including implement and no class is given
- **THEN** route seat exits 3 with `ambiguous_class`

#### Scenario: Explicit implement class
- **WHEN** that caller gives `--class implement`
- **THEN** route seat resolves the implement entry

### Requirement: Setup errors are typed
The router SHALL distinguish rule, seat and class setup failures with typed errors.

#### Scenario: No rules
- **WHEN** no usable APPLIED and no installed table exist
- **THEN** route seat exits 3 with `no_rules`

#### Scenario: Unknown seat
- **WHEN** a seat is absent from the seats map
- **THEN** route seat exits 3 with `unknown_seat`

#### Scenario: Old table
- **WHEN** a commit has no seats map
- **THEN** route seat exits 3 with `unknown_rules`

### Requirement: Workflows read routing data in one call
The router SHALL return seats, chains, audit, escalation and rules together without an LB call.

#### Scenario: Read all seats
- **WHEN** a workflow invokes `route seats`
- **THEN** it receives rules, seats, chains, audit and the implement escalation seat and fix round

### Requirement: Implement escalation is data
The verifier SHALL require an audited implement chain seat for escalation.

#### Scenario: Invalid escalation seat
- **WHEN** escalation names a seat outside the audited implement chain
- **THEN** verification fails `escalation`

### Requirement: Doctor reports the applied rules
The doctor SHALL show the applied commit and its distance from origin.

#### Scenario: One commit behind
- **WHEN** APPLIED is one commit behind origin
- **THEN** doctor reports `behind` as 1
